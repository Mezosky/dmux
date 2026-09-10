"""Experiment details and bounded previews of explicitly configured outputs."""
from __future__ import annotations

from .mouse import help_hint, target as mouse_target

from collections import OrderedDict
import json
import os
import time
from itertools import islice
from pathlib import Path

from .connectors import tail_log
from .ui import COLORS, content_height, duration, progress_text, selected_task, task_label


class PreviewReader:
    """Bounded file tails and short-lived glob discovery for an opened detail."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.files = OrderedDict()
        self.matches = OrderedDict()

    def clear(self):
        self.files.clear()
        self.matches.clear()

    def paths(self, base, pattern):
        key, now = (base, pattern), self.clock()
        cached = self.matches.get(key)
        if cached is None or now - cached[0] >= 2:
            path = Path(pattern)
            try:
                matches = [path] if path.is_absolute() else list(islice(base.glob(pattern), 8))
            except OSError:
                matches = []
            self.matches[key] = (now, matches)
        self.matches.move_to_end(key)
        while len(self.matches) > 16:
            self.matches.popitem(last=False)
        return self.matches[key][1]

    def tail(self, path, **options):
        if not path:
            return []
        path = Path(path)
        key = (path, tuple(sorted(options.items())))
        try:
            info = path.stat()
        except OSError:
            self.files.pop(key, None)
            return []
        stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        if key not in self.files or self.files[key][0] != stamp:
            self.files[key] = (stamp, tail_log(path, **options))
        self.files.move_to_end(key)
        while len(self.files) > 16:
            self.files.popitem(last=False)
        return self.files[key][1]


def output_previews(task, reader=None):
    """Read only configured files, with bounded glob matches and text tails."""
    if not task.get("directory"):
        return []
    reader = reader or PreviewReader()
    base = Path(task["directory"])
    previews = []
    seen = set()
    for pattern in task.get("outputs", [])[:8]:
        matches = reader.paths(base, pattern)
        for match in matches:
            if match in seen:
                continue
            seen.add(match)
            try:
                if not match.is_file():
                    continue
                size = match.stat().st_size
            except OSError:
                continue
            text = (reader.tail(match, n=5, max_bytes=4096, max_line_length=180)
                    if match.suffix.lower() in {".json", ".jsonl", ".log", ".txt", ".csv"} else [])
            previews.append((str(match), size, text))
            if len(previews) >= 8:
                return previews
    return previews


def render_detail(snapshot, model, *, presentation, stage=None, height=40, width=80, notice=None,
                  metric_reader=None, metric_offset=0, return_home=False, preview_reader=None):
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    preview_reader = preview_reader or PreviewReader()
    task = selected_task(snapshot, model, stage)
    title = Text(f"DMUX / {presentation.entity_name(model)}", style="bold bright_cyan")
    if task is None:
        return Panel(Group(title, Text("No stages available. Esc returns to experiments.")))
    parts = [title, Text("Results: " + str(task.get("directory") or "not configured"), style="grey74",
                        overflow="ellipsis", no_wrap=True)]
    footer = []
    for warning in snapshot.get("warnings", [])[:2]:
        footer.append(Text("! " + warning, style="yellow"))
    if notice:
        footer.append(Text(notice, style="yellow", overflow="ellipsis", no_wrap=True))
    navigation = help_hint("detail")
    footer.append(navigation)

    def remaining(extra=()):
        return height - content_height(Panel(Group(*parts, *extra, *footer)), width)

    tasks = [item for item in snapshot["tasks"] if item["model"] == model]
    has_metrics = bool(task.get("metrics"))
    index = tasks.index(task)

    def stage_table(count):
        table = Table(expand=True, box=None)
        for heading in ("STAGE", "STATE", "PID", "ELAPSED"):
            table.add_column(heading)
        start = min(max(0, index - count // 2), len(tasks) - count)
        for item in tasks[start:start + count]:
            table.add_row(Text(("› " if item is task else "  ") + task_label(item, presentation)),
                          Text(item["state"], style=COLORS.get(item["state"], "white")),
                          ", ".join(str(p["pid"]) for p in item.get("processes", [])) or str(item["pid"] or "—"),
                          duration(item["elapsed_seconds"]), style=mouse_target("stage", item["name"]))
        return table

    # Reserve the selected stage and results before expanding its neighbors.
    table = stage_table(1)
    stage_position = len(parts)
    parts.append(table)
    progress = task.get("progress")
    if progress is not None and task["expected"] is not None:
        text = Text(f'Stage progress: {progress["saved"]:,} / {task["expected"]:,} saved  ')
        text.append_text(progress_text(progress["saved"], task["expected"]))
        parts.append(text)
    elif progress is not None:
        parts.append(Text(f'Stage progress: {(progress or {}).get("saved", 0):,} saved · total unknown'))
    if has_metrics:
        from .metrics import MetricReader, render_metrics

        metric_reader = metric_reader or MetricReader()
        # A metric has a value, a plot/status line, and at most one warning
        # line. Reserve that shape so page size cannot change with page contents.
        status_rows = content_height(Text("No valid points yet; check the configured field"), max(1, width - 8))
        limit = max(1, min(3, (remaining() - 2) // (2 + status_rows)))
        if len(task["metrics"]) > limit:
            navigation.append(" · m next metrics", style=mouse_target("key", "m"))
        parts.append(render_metrics(task, metric_reader, width=max(1, width - 4),
                                    limit=limit, offset=metric_offset))
    for count in range(min(len(tasks), 4 if has_metrics else 8), 1, -1):
        parts[stage_position] = stage_table(count)
        if remaining() >= 0:
            break
        parts[stage_position] = table
    if task.get("process_command") and remaining() >= 1:
        parts.append(Text("Process: " + " ".join(task["process_command"]), style="grey62",
                          no_wrap=True, overflow="ellipsis"))
    metadata = task.get("metadata") or {}
    if remaining() >= 1 and task.get('processes'):
        from .resources import summarize
        resource = summarize(task, snapshot.get('gpu', {}))
        cpu = f'{resource["cpu_percent"]:.1f}%' if resource['cpu_percent'] is not None else 'warming up / unavailable'
        rss = f'{resource["rss_bytes"] / 1024**2:.1f} MiB' if resource['rss_bytes'] is not None else 'unknown'
        gpu = f'{resource["gpu_bytes"] / 1024**2:.1f} MiB' if resource['gpu_bytes'] is not None else 'unknown'
        parts.append(Text(f'Matched PIDs: CPU {cpu} / RSS {rss} / GPU {gpu}' + (' (stale)' if resource['gpu_stale'] else ''),
                          overflow='ellipsis', no_wrap=True))
    if metadata:
        entries = list(metadata.items())[:5]
        for count in range(len(entries), 0, -1):
            lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in entries[:count]]
            panel = Panel(Text("\n".join(lines), overflow="ellipsis", no_wrap=True),
                          title="Metadata", border_style="grey35")
            if remaining([panel]) >= 0:
                parts.append(panel)
                break
    # Read optional sources only when a panel can fit. Reserve room for both
    # outputs and the configured log instead of consuming all rows with previews.
    if remaining() >= 3:
        recent = preview_reader.tail(task["log"], n=3)
        recent_panel = (Panel(Text("\n".join(recent), overflow="ellipsis", no_wrap=True),
                              title="Recent log", border_style="grey35") if recent else None)
        reserve = content_height(recent_panel, max(1, width - 4)) if recent_panel else 0
        if remaining() < reserve + 3:
            reserve = 0
        previews = output_previews(task, preview_reader)
        entries = [(os.path.relpath(name, task["directory"]), size, preview)
                   for name, size, preview in previews[:4]]
        # Preserve filenames before adding tail lines, so long rows do not hide
        # which configured source is being shown.
        candidates = [(count, depth) for count in range(len(entries), 0, -1)
                      for depth in (2, 1, 0)] if entries else [(0, 0)]
        for count, depth in candidates:
            lines = []
            for name, size, preview in entries[:count]:
                lines.append(f"{name} ({size:,} bytes)")
                lines.extend(preview[-depth:] if depth else [])
            if not lines:
                lines = ["No configured outputs are present yet." if task.get("outputs")
                         else "No output previews configured; add outputs to this task in plan.json."]
            panel = Panel(Text("\n".join(lines), overflow="ellipsis", no_wrap=True),
                          title="Outputs", border_style="grey35")
            if remaining([panel]) >= reserve:
                parts.append(panel)
                break
        if recent_panel:
            for count in range(len(recent), 0, -1):
                panel = Panel(Text("\n".join(recent[-count:]), overflow="ellipsis", no_wrap=True),
                              title="Recent log", border_style="grey35")
                if remaining([panel]) >= 0:
                    parts.append(panel)
                    break
    parts.extend(footer)
    return Panel(Group(*parts), border_style="grey35")


def render_stop_confirmation(request, typed, *, height=24):
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    text = Text(f"Stop {request.label}?\n", style="bold red")
    text.append(f"SIGTERM will be sent to {len(request.targets)} processes, including children:\n", style="white")
    limit = max(1, height - 11)
    for target in request.targets[:limit]:
        text.append(f"  PID {target.pid}: {' '.join(target.command)[:100]}\n", style="grey74")
    if len(request.targets) > limit:
        text.append(f"  … and {len(request.targets) - limit} more. Enlarge the terminal to review all.\n", style="yellow")
    text.append("Chats and results are kept. A scheduler, if used, may schedule more work.\n", style="yellow")
    text.append(f"Type {request.label} and press Enter: ", style="white")
    text.append(typed + "▏", style="bold bright_cyan")
    return Panel(Group(text, Text("Esc cancels; no automatic force kill", style="grey70")), border_style="red")
