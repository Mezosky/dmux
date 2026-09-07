"""Bounded, read-only hints for connecting existing result files."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from itertools import islice
import json
import os
from pathlib import Path
from .connectors import open_regular


def sample_file(path: Path, max_bytes: int = 65_536) -> tuple[str, dict | None]:
    """Inspect a small prefix; never load weights or execute project code."""
    fallback = "jsonl" if path.suffix.lower() == ".jsonl" else "json"
    raw = b""
    try:
        if not path.is_file():
            return fallback, None
        with open_regular(path) as handle:
            raw = handle.read(max_bytes)
        if fallback != "jsonl":
            value = json.loads(raw)
            if isinstance(value, dict):
                return "json", value
    except (OSError, ValueError, UnicodeError):
        pass
    # Some producers use .json for newline-delimited JSON. Require a committed
    # object line; an unfinished final record is never used as a sample.
    for line in raw.split(b"\n")[:-1][:8]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return "jsonl", value
        except (ValueError, UnicodeError):
            continue
    return fallback, None


def integer_fields(value: Mapping | None, *, limit: int = 64) -> list[str]:
    fields = []
    pending = deque([(value, "", 0)])
    visited = 0
    while pending and visited < limit:
        item, prefix, depth = pending.popleft()
        if not isinstance(item, Mapping) or depth > 6:
            continue
        for key, child in islice(item.items(), limit - visited):
            visited += 1
            if not isinstance(key, str) or "." in key:
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            if type(child) is int and child >= 0:
                fields.append(dotted)
            elif isinstance(child, Mapping):
                pending.append((child, dotted, depth + 1))
    return fields


def discover_outputs(root: Path, *, limit: int = 500, depth: int = 2) -> tuple[list[Path], bool]:
    """List candidate files without unbounded traversal or following directory links."""
    found = []
    pending = deque([(root, 0)])
    remaining = limit
    while pending and remaining:
        directory, level = pending.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining -= 1
                    if not entry.name.startswith("."):
                        if entry.is_dir(follow_symlinks=False) and level < depth:
                            pending.append((Path(entry.path), level + 1))
                        elif entry.is_file(follow_symlinks=False):
                            path = Path(entry.path)
                            if path.suffix.lower() in {".json", ".jsonl", ".log", ".csv", ".ckpt"} or path.name == "DONE":
                                found.append(path.relative_to(root))
                    if not remaining:
                        break
        except OSError:
            continue
    return sorted(found, key=lambda p: (len(p.parts), str(p))), bool(pending or not remaining)


def suggest(fields: list[str], candidates: tuple[str, ...], default: str = "") -> str:
    return next((name for name in candidates if name in fields), default)
