"""Experiment details and bounded previews of explicitly configured outputs."""
from __future__ import annotations

import json
from itertools import islice
from pathlib import Path

from .connectors import tail_log
from .ui import COLORS, duration, progress_text, task_label


def selected_task(snapshot, model, stage=None):
    tasks = [task for task in snapshot["tasks"] if task["model"] == model]
    return (next((task for task in tasks if task["name"] == stage), None)
            or next((task for task in tasks if task["pid"]), None)
            or next((task for task in tasks if task["state"] != "complete"), None)
            or next(iter(tasks), None))


def output_previews(task):
    """Read only configured files, with bounded glob matches and text tails."""
    if not task.get("directory"):
        return []
    base = Path(task["directory"])
    previews = []
    seen = set()
    for pattern in task.get("outputs", [])[:8]:
        path = Path(pattern)
        matches = [path] if path.is_absolute() else islice(base.glob(pattern), 8)
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
            text = (tail_log(match, n=5, max_bytes=4096, max_line_length=180)
                    if match.suffix.lower() in {".json", ".jsonl", ".log", ".txt", ".csv"} else [])
            previews.append((str(match), size, text))
            if len(previews) >= 8:
                return previews
    return previews


def render_detail(snapshot, model, *, presentation, stage=None, height=40, width=80, notice=None,
                  metric_reader=None, metric_offset=0, return_home=False):
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    task = selected_task(snapshot, model, stage)
    title = Text(f"DMUX / {presentation.entity_name(model)}", style="bold bright_cyan")
    if task is None:
        return Panel(Group(title, Text("No stages available. Esc returns to experiments.")))
    parts = [title, Text("Results: " + str(task.get("directory") or "not configured"), style="grey74",
                        overflow="ellipsis", no_wrap=True)]
    table = Table(expand=True, box=None)
    for heading in ("STAGE", "STATE", "PID", "ELAPSED"):
        table.add_column(heading)
    tasks = [item for item in snapshot["tasks"] if item["model"] == model]
    has_metrics = bool(task.get("metrics"))
    # Keep the selected stage in view even for a large pipeline.
    index = tasks.index(task)
    count = max(1, min(2 if has_metrics and height < 40 else 4 if has_metrics else 8, height // 5))
    start = max(0, index - count // 2)
    for item in tasks[start:start + count]:
        table.add_row(Text(("› " if item is task else "  ") + task_label(item, presentation)),
                      Text(item["state"], style=COLORS.get(item["state"], "white")),
                      ", ".join(str(p["pid"]) for p in item.get("processes", [])) or str(item["pid"] or "—"),
                      duration(item["elapsed_seconds"]))
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

        parts.append(render_metrics(task, metric_reader or MetricReader(), width=width,
                                    limit=1 if height < 24 else 2 if height < 40 else 3,
                                    offset=metric_offset))
    if task.get("process_command"):
        parts.append(Text("Process: " + " ".join(task["process_command"]), style="grey62",
                          no_wrap=True, overflow="ellipsis"))
    metadata = task.get("metadata") or {}
    if metadata and (not has_metrics or height >= 32):
        # Per-field lines preserve the values on compact terminals.
        lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in list(metadata.items())[:2 if has_metrics else 5]]
        parts.append(Panel(Text("\n".join(lines), overflow="ellipsis"), title="Metadata", border_style="grey35"))
    if height >= (45 if has_metrics else 28):
        previews = output_previews(task)
        lines = []
        for name, size, preview in previews[:2 if height < 45 else 4]:
            lines.append(f"{name} ({size:,} bytes)")
            lines.extend(preview[:2])
        if not lines:
            lines = ["No configured outputs are present yet." if task.get("outputs")
                     else "No output previews configured; add outputs to this task in plan.json."]
        parts.append(Panel(Text("\n".join(lines), overflow="ellipsis"), title="Outputs", border_style="grey35"))
    if height >= (55 if has_metrics else 40):
        recent = tail_log(task["log"], n=3)
        if recent:
            parts.append(Panel(Text("\n".join(recent)), title="Recent log", border_style="grey35"))
    if notice:
        parts.append(Text(notice, style="yellow", overflow="ellipsis", no_wrap=True))
    parts.append(Text("[ ] stage · t tmux · k stop stage · K stop experiment", style="grey70"))
    parts.append(Text(("Esc/q home" if return_home else "Esc/q back") +
                      " · m next results · x hide tab (u restores tabs)", style="grey70"))
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
