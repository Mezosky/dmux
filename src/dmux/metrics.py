"""Lazy, bounded result summaries and terminal sparklines. Never progress counters."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import csv
import io
import json
import math
import os
import stat

from .adapters.base import resolve_path


def field(value, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value


def validate_metrics(metrics):
    if not isinstance(metrics, list) or len(metrics) > 12:
        raise ValueError("task.metrics must be a list of at most 12 metric definitions")
    for config in metrics:
        if not isinstance(config, Mapping):
            raise ValueError("each metric must be an object")
        for name in ("label", "path", "field"):
            if not isinstance(config.get(name), str) or not config[name]:
                raise ValueError(f"metric.{name} must be a non-empty string")
        if config.get("type", "jsonl") not in ("json", "jsonl", "csv"):
            raise ValueError("metric.type must be json, jsonl or csv")
        for name in ("x_field", "records_field", "unit"):
            if name in config and not isinstance(config[name], str):
                raise ValueError(f"metric.{name} must be a string")
        if config.get("goal") not in (None, "min", "max"):
            raise ValueError("metric.goal must be min or max (omit when neither applies)")
        if type(config.get("precision", 4)) is not int or not 0 <= config.get("precision", 4) <= 8:
            raise ValueError("metric.precision must be an integer from 0 to 8")
        scale = config.get("scale", 1)
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            raise ValueError("metric.scale must be a positive finite number")


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


class MetricReader:
    """Cache only opened result sources, with at most 8 files and 256 points each.

    Changed JSONL/CSV sources use a bounded tail; whole JSON is capped at 256 KiB.
    No monitor snapshot or home-screen refresh instantiates or calls this reader.
    """
    max_bytes = 256 * 1024
    max_points = 256

    def __init__(self):
        self.cache = OrderedDict()

    def clear(self):
        self.cache.clear()

    def _source(self, path, kind, records_field):
        key = (path, kind, records_field)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("not a regular file")
                stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
                if key in self.cache and self.cache[key][0] == stamp:
                    self.cache.move_to_end(key)
                    return self.cache[key][1]
                limited = info.st_size > self.max_bytes
                if kind == "json":
                    if limited:
                        raise ValueError("JSON exceeds 256 KiB; use a smaller summary or JSONL/CSV history")
                    raw = handle.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise ValueError("JSON grew beyond 256 KiB")
                    value = json.loads(raw)
                    records = field(value, records_field) if records_field else value
                    if isinstance(records, dict):
                        records = [records]
                    if not isinstance(records, list):
                        raise ValueError("JSON must be an object or an array of records; check records_field")
                    limited = len(records) > self.max_points
                    warnings = []
                    records = records[-self.max_points:]
                else:
                    header = None
                    if kind == "csv":
                        header = handle.readline(16_384)
                        if not header.endswith(b"\n"):
                            raise ValueError("CSV needs a complete header line (maximum 16 KiB)")
                    start = max(handle.tell(), info.st_size - self.max_bytes)
                    handle.seek(start)
                    if start > (len(header) if header else 0):
                        # Skip a potentially cut first line, even when it happens to be complete.
                        handle.readline(self.max_bytes)
                    raw = handle.read(self.max_bytes)
                    partial = bool(raw and not raw.endswith(b"\n"))
                    lines = raw.split(b"\n")[:-1]
                    limited = limited or len(lines) > self.max_points
                    lines = lines[-self.max_points:]
                    warnings = ["uncommitted final row excluded"] if partial else []
                    records, malformed = [], 0
                    if kind == "csv":
                        text = header.decode("utf-8-sig") + "\n".join(line.decode("utf-8") for line in lines)
                        # One CSV record per physical line; multiline fields aren't a metric-log contract.
                        parsed = list(csv.DictReader(io.StringIO(text), strict=True))
                        for row in parsed:
                            if None in row or any(v is None for v in row.values()):
                                malformed += 1
                            else:
                                records.append(row)
                    else:
                        for line in lines:
                            if not line.strip():
                                continue
                            try:
                                row = json.loads(line)
                                if not isinstance(row, dict):
                                    raise ValueError("record is not an object")
                                records.append(row)
                            except (ValueError, UnicodeError):
                                malformed += 1
                    if malformed:
                        warnings.append(f"{malformed} malformed row(s) excluded")
                result = {"records": records, "warnings": warnings, "limited": limited, "error": None}
                self.cache[key] = (stamp, result)
                self.cache.move_to_end(key)
                while len(self.cache) > 8:
                    self.cache.popitem(last=False)
                return result
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            self.cache.pop(key, None)  # Never present stale cached metrics as current after an error.
            return {"records": [], "warnings": [], "limited": False,
                    "error": "not generated yet" if isinstance(exc, FileNotFoundError) else str(exc)}

    def read(self, directory, config):
        source = resolve_path(config["path"], directory)
        data = self._source(source, config.get("type", "jsonl"), config.get("records_field", ""))
        points, ignored, seen = [], 0, set()
        x_field = config.get("x_field")
        scale = config.get("scale", 1)
        for row in data["records"]:
            lookup = row.get if config.get("type") == "csv" else lambda key: field(row, key)
            y = number(lookup(config["field"]))
            x = number(lookup(x_field)) if x_field else len(points)
            if y is None or x is None or not math.isfinite(y * scale):
                ignored += 1
                continue
            if x_field and (x in seen or points and x < points[-1][0]):
                ignored += 1
                continue
            seen.add(x)
            points.append((x, y * scale))
        warnings = list(data["warnings"])
        if ignored:
            warnings.append(f"{ignored} missing/non-finite/duplicate/out-of-order point(s) excluded")
        values = [y for _, y in points]
        goal = config.get("goal")
        return {"config": config, "source": str(source), "points": points,
                "latest": values[-1] if values else None,
                "best": (min(values) if goal == "min" else max(values)) if values and goal else None,
                "low": min(values) if values else None, "high": max(values) if values else None,
                "warnings": warnings, "limited": data["limited"], "error": data["error"]}


def sparkline(values, width=28):
    """Sample-order sparkline with per-bucket extrema, not an elapsed-time axis."""
    width = max(2, width)
    values = list(values)
    if not values:
        return ""
    low, high = min(values), max(values)
    if len(values) > width:
        buckets = max(1, width // 2)
        sampled = []
        for i in range(buckets):
            chunk = values[i * len(values) // buckets:(i + 1) * len(values) // buckets]
            indices = sorted({chunk.index(min(chunk)), chunk.index(max(chunk))})
            sampled.extend(chunk[index] for index in indices)
        values = sampled
    blocks = "▁▂▃▄▅▆▇█"
    if high == low:
        return "▄" * len(values)
    # Scale before subtracting to avoid overflow for opposite-sign finite extremes.
    divisor = max(abs(low), abs(high), 1)
    return "".join(blocks[min(7, max(0, round(7 * (v / divisor - low / divisor) / (high / divisor - low / divisor))))]
                   for v in values)


def render_metrics(task, reader, *, width=80, limit=3, offset=0):
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    configs = task.get("metrics", [])
    if not configs or not task.get("directory"):
        return None
    start = (offset % ((len(configs) + limit - 1) // limit)) * limit
    chosen = configs[start:start + limit]
    lines = []
    for config in chosen:
        data = reader.read(task["directory"], config)
        precision, unit = config.get("precision", 4), config.get("unit", "")[:12]
        def fmt(value):
            if value is None:
                return "—"
            compact = abs(value) >= 1_000_000 or 0 < abs(value) < 10 ** -precision
            return (f"{value:.3g}" if compact else f"{value:.{precision}f}") + unit
        line = Text(config["label"][:28] + "  ", style="bold cyan", overflow="ellipsis", no_wrap=True)
        line.append(fmt(data["latest"]), style="bold white")
        if data["best"] is not None:
            line.append("  best/window " + fmt(data["best"]), style="grey70")
        lines.append(line)
        if data["error"]:
            lines.append(Text(data["error"], style="yellow", overflow="ellipsis", no_wrap=True))
        elif len(data["points"]) > 1:
            chart = Text(sparkline([y for _, y in data["points"]], width=max(8, min(32, width // 3))), style="bright_cyan",
                         overflow="ellipsis", no_wrap=True)
            chart.append(f'  {fmt(data["low"])}…{fmt(data["high"])} · {len(data["points"])} pts', style="grey62")
            lines.append(chart)
        else:
            lines.append(Text("Latest value only · no history" if data["points"] else "No valid points yet; check the configured field", style="grey62"))
        if data["warnings"]:
            lines.append(Text("; ".join(data["warnings"]), style="yellow", overflow="ellipsis", no_wrap=True))
    caption = "Results · recent samples (not time-scaled)"
    if len(configs) > limit:
        caption += f" · {start + 1}–{start + len(chosen)}/{len(configs)} · m next"
    return Panel(Group(*lines), title=Text(caption), border_style="cyan", padding=(0, 1))
