"""Responsive Rich terminal views for adapter-neutral snapshots."""
from __future__ import annotations

from datetime import datetime, timezone

from .adapters.base import Presentation
from .bindings import legend


COLORS = {
    "running": "bright_cyan",
    "complete": "green",
    "queued": "grey50",
    "partial": "yellow",
    "interrupted": "yellow",
    "failed": "red",
    "invalid": "red",
    "blocked": "yellow",
    "starting": "bright_cyan",
    "paused": "yellow",
    "idle": "grey70",
    "pause requested": "yellow",
    "waiting": "grey70",
    "unknown": "yellow",
}


def content_height(renderable, width):
    """Measure wrapped Rich content at its actual available column width."""
    from rich.console import Console

    console = Console(width=max(1, width), color_system=None)
    return len(console.render_lines(renderable, console.options, pad=False))


def selected_task(snapshot, model, stage=None):
    """Resolve explicit, live, attention, pending, then last completed stage."""
    tasks = [task for task in snapshot["tasks"] if task["model"] == model]
    return (next((task for task in tasks if task["name"] == stage), None)
            or next((task for task in tasks if task["pid"]), None)
            or next((task for task in tasks if task["state"] not in {"complete", "queued"}), None)
            or next((task for task in tasks if task["state"] != "complete"), None)
            or (tasks[-1] if tasks else None))


def duration(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600:02d}h"


def progress_text(saved, total, *, width=12):
    from rich.text import Text

    fraction = min(1.0, max(0.0, saved / total)) if total else 0.0
    filled = round(width * fraction)
    text = Text("━" * filled, style="bright_cyan")
    text.append("─" * (width - filled), style="grey30")
    text.append(f" {100 * fraction:5.1f}%", style="bright_cyan" if saved else "grey50")
    return text


def state_cell(task):
    from rich.text import Text

    if not task:
        return Text("—", style="grey35")
    state = task["state"]
    if state == "complete":
        return Text("✓", style="green")
    if state in {"failed", "invalid"}:
        return Text("ERROR", style="bold red")
    progress = task["progress"]
    if progress and task["expected"] and progress["saved"]:
        prefix = "▶" if state == "running" else "·"
        return Text(
            f'{prefix}{100 * progress["saved"] / task["expected"]:.0f}%',
            style=COLORS.get(state, "yellow"),
        )
    return Text(
        "RUN" if state == "running" else "WAIT" if state in {"queued", "starting"} else state.upper(),
        style=COLORS.get(state, "grey50"),
    )


def task_label(task, presentation: Presentation) -> str:
    return task.get("label") or presentation.stage_label(task["name"])


def experiment_tabs(models, selected: str, presentation: Presentation, width: int):
    """Render a compact, paged tab strip centered on the selected experiment."""

    from rich.text import Text

    tags = [model["tag"] for model in models]
    if not tags:
        return Text()
    selected_index = tags.index(selected) if selected in tags else 0
    from rich.cells import cell_len

    # Page around selection, sharing spare columns among labels that need them.
    capacity = max(1, min(len(tags), (width - 16) // 12))
    start = min(max(0, selected_index - capacity // 2), max(0, len(tags) - capacity))
    visible = tags[start:start + capacity]
    tabs = Text("EXPERIMENTS  ", style="bold grey62", no_wrap=True)
    if start:
        tabs.append("…  ", style="grey50")
    suffix = "…" if start + len(visible) < len(tags) else ""
    available = max(1, width - tabs.cell_len - len(suffix) - 3 * len(visible))
    names = [presentation.entity_name(tag) for tag in visible]
    lengths = [cell_len(name) for name in names]
    allocations = [0] * len(names)
    while available and any(a < size for a, size in zip(allocations, lengths)):
        for i, size in enumerate(lengths):
            if available and allocations[i] < size:
                allocations[i] += 1
                available -= 1
    for tag, name, allocation in zip(visible, names, allocations):
        label = Text(name)
        label.truncate(allocation, overflow="ellipsis")
        style = "bold black on bright_cyan" if tag == selected else "grey74 on grey15"
        tabs.append(" " + label.plain + " ", style=style)
        tabs.append(" ")
    tabs.append(suffix, style="grey50")
    return tabs


def render_dashboard(
    snapshot,
    *,
    width=120,
    height=50,
    selected=None,
    stage=None,
    expanded=False,
    notice=None,
    presentation: Presentation | None = None,
):
    from rich import box
    from rich.console import Group, RenderableType
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if presentation is None:
        if snapshot.get("presentation"):
            presentation = Presentation(**snapshot["presentation"])
        else:
            # Hand-built snapshots remain usable without importing any adapter.
            stages = tuple(dict.fromkeys(task["name"] for task in snapshot.get("tasks", []))) or ("run",)
            labels = {name: name.replace("_", " ").title() for name in stages}
            presentation = Presentation(
                name=snapshot.get("adapter", "Experiments"), stages=stages,
                labels=labels, short_labels=tuple(label[:8] for label in labels.values()),
            )
    color = COLORS.get(snapshot["state"], "white")
    now = datetime.fromtimestamp(snapshot["updated"], timezone.utc).strftime("%H:%M:%S UTC")
    title = Text("DMUX", style="bold bright_cyan")
    title.append(f"  /  {presentation.name.upper()}", style="bold white")
    title.append(f'    ● {snapshot["state"].upper()}', style=color)
    title.append(f"    {now}", style="grey62")
    parts: list[RenderableType] = [Panel(title, border_style="grey35", box=box.ROUNDED, padding=(0, 1))]
    if "expected" not in snapshot:
        parts += [Text(snapshot["message"], style="yellow"), Text(snapshot["queue"], style="grey62")]
        return Group(*parts)

    if snapshot["expected"] is None:
        summary = Text(f'{snapshot["saved"]:,} {presentation.unit} saved · total unknown', style="bold")
    else:
        summary = Text(
            f'{snapshot["saved"]:,} / {snapshot["expected"]:,} {presentation.unit} saved   ', style="bold")
        summary.append_text(
            progress_text(snapshot["saved"], snapshot["expected"], width=16 if width >= 100 else 8))
    summary.append(
        f'   {snapshot["completed_stages"]}/{snapshot["total_stages"]} stages complete',
        style="grey74",
    )
    parts.append(summary)
    health = Text(
        f'{snapshot["valid"]:,} accepted · {snapshot["excluded"]:,} excluded', style="grey70"
    )
    devices = snapshot["gpu"]["devices"]
    if devices:
        gpu = devices[0]
        health.append(
            f'   GPU {gpu["utilization"]:.0f}% · {gpu["used_gib"]:.1f}/{gpu["total_gib"]:.1f} GiB',
            style="bright_cyan",
        )
        if len(devices) > 1:
            health.append(f" (+{len(devices) - 1} GPUs)", style="grey62")
    else:
        health.append("   GPU disabled" if snapshot["gpu"].get("error") == "disabled"
                      else "   GPU unavailable", style="grey62")
    health.append(
        f'   Disk {snapshot["disk_free_gib"]:.1f} GiB free',
        style="yellow" if snapshot["disk_free_gib"] < 20 else "grey70",
    )
    parts.append(health)
    gpu_error = snapshot["gpu"].get("error")
    if gpu_error and gpu_error != "disabled":
        explanation = str(gpu_error)
        if snapshot["gpu"].get("stale"):
            explanation += " · showing last good reading"
        parts.append(Text("GPU: " + explanation, style="yellow"))
    elif not devices and not gpu_error:
        parts.append(Text("GPU: no devices reported", style="grey62"))

    active = snapshot.get("active")
    if not snapshot["models"]:
        parts.append(Text("All experiment tabs are hidden. Press u to restore them."
                          if snapshot.get("hidden_count") else "No experiments are declared in this plan yet.",
                          style="yellow"))
        parts.append(Text(("u restore · " if snapshot.get("hidden_count") else "") +
                          "q quit · r refresh · ? help · read-only", style="grey58", no_wrap=True))
        return Group(*parts)
    selected = selected or (active["model"] if active else snapshot["models"][0]["tag"])
    parts.append(experiment_tabs(snapshot["models"], selected, presentation, width))
    overview = Table(box=box.SIMPLE_HEAD, expand=True, padding=(0, 1), show_edge=False)
    overview.add_column(presentation.entity_heading, no_wrap=True)
    overview.add_column("SAVED", justify="right", no_wrap=True)
    narrow = width < 100
    if narrow:
        overview.add_column("STAGES", justify="right")
        overview.add_column("CURRENT STATE")
    else:
        for short_label in presentation.short_labels:
            overview.add_column(short_label, justify="center", no_wrap=True)
    for model in snapshot["models"]:
        tag = model["tag"]
        label = Text(
            ("› " if selected == tag else "  ") + presentation.entity_name(tag),
            style="bold white" if selected == tag else "grey74",
        )
        model_tasks = {task["name"]: task for task in snapshot["tasks"] if task["model"] == tag}
        if model["blocked"]:
            saved = Text("blocked", style="yellow")
        elif model["expected"]:
            saved = Text(
                f'{100 * model["saved"] / model["expected"]:.1f}%',
                style="bright_cyan" if model["saved"] else "grey50",
            )
        else:
            saved = Text(f'{model["saved"]:,} saved' if model.get("counted") else "—", style="grey70")
        if narrow:
            current = next(
                (
                    task
                    for task in model_tasks.values()
                    if task["state"] in {"running", "starting", "failed", "invalid", "interrupted"}
                ),
                None,
            )
            current_state = (
                model.get("reason") or "No stages declared"
                if model["blocked"]
                else task_label(current, presentation)
                if current
                else "complete"
                if model["completed_stages"] == model["stages"]
                else "queued"
            )
            overview.add_row(
                label,
                saved,
                f'{model["completed_stages"]}/{model["stages"]}' if model_tasks else "—",
                current_state,
            )
        else:
            overview.add_row(
                label,
                saved,
                *[state_cell(model_tasks.get(name)) for name in presentation.stages],
            )
    parts.append(overview)

    focus = [task for task in snapshot["tasks"] if task["model"] == selected]
    current = selected_task(snapshot, selected, stage)
    if current:
        info = Text(
            f'{presentation.entity_name(selected)} · {task_label(current, presentation)}',
            style="bold bright_cyan",
        )
        info.append(f' · {current["state"]}', style=COLORS.get(current["state"], "white"))
        if current["pid"]:
            info.append(
                f' · PID {current["pid"]} · elapsed {duration(current["elapsed_seconds"])}',
                style="grey74",
            )
        parts.append(info)
        stage_progress = Text("Stage progress: ", style="bold grey74")
        if current["expected"] is not None and current["progress"] is not None:
            saved_count, expected = current["progress"]["saved"], current["expected"]
            stage_progress.append(f"{saved_count:,} / {expected:,} saved   ", style="bold white")
            stage_progress.append_text(
                progress_text(saved_count, expected, width=16 if width >= 100 else 8)
            )
            if width >= 100:
                stage_progress.append(f"   {max(0, expected - saved_count):,} remaining", style="grey70")
        elif current["progress"] is not None:
            stage_progress.append(f'{(current["progress"] or {}).get("saved", 0):,} saved · total unknown')
        else:
            stage_progress.append(current["state"], style=COLORS.get(current["state"], "white"))
            stage_progress.append(
                " · completion artifact present"
                if current["state"] == "complete"
                else " · no incremental counter",
                style="grey62",
            )
        parts.append(stage_progress)

    if "tmux" in snapshot:
        tmux = snapshot["tmux"]
        link = tmux["associations"].get(selected, {})
        pane = link.get("pane")
        linked = (
            f'{pane["label"]} ({link["source"]})'
            if pane
            else f'missing link: {link["target"]}'
            if link.get("target")
            else "no sessions on this socket"
            if not tmux["panes"]
            else "no pane linked to this run"
        )
        parts.append(
            Text("tmux: " + linked + " · t browse", style="grey70", overflow="ellipsis", no_wrap=True)
        )

    footer = [Text("! " + warning, style="yellow") for warning in snapshot["warnings"][:2]]
    if notice:
        footer.append(Text(notice, style="yellow", overflow="ellipsis", no_wrap=True))
    keys = "n/p tabs · Enter details · t tmux · k/K stop · q quit · ? help"
    if expanded or width >= 160:
        keys = legend("dashboard")
    suffix = " · read-only"
    if width >= 120:
        suffix = " · read-only monitoring; stops require confirmation"
    footer.append(Text(keys + suffix, style="grey58", no_wrap=True, overflow="ellipsis"))

    if expanded or content_height(Group(*parts, *footer), width) + 3 <= height:
        steps = Table(box=None, expand=True, padding=(0, 1))
        steps.add_column("STAGE")
        steps.add_column("STATE", justify="right")
        steps.add_column("SAVED / TOTAL", justify="right")
        for task in focus:
            count = task["progress"]["saved"] if task["progress"] else 0
            number = (
                f'{count:,} / {task["expected"]:,}'
                if task["expected"] is not None
                else f"{count:,} / —"
                if task.get("counted")
                else "artifact"
                if task["state"] == "complete"
                else "—"
            )
            steps.add_row(
                task_label(task, presentation),
                Text(task["state"], style=COLORS.get(task["state"], "white")),
                number,
            )
        if not focus:
            blocked_model: dict = next((model for model in snapshot["models"] if model["tag"] == selected), {})
            steps.add_row("No stages declared", Text("blocked", style="yellow"), "—")
            steps.caption = blocked_model.get("reason", "Not scheduled")

        progress = (current["progress"] or {}) if current else {}
        breakdown = progress.get("breakdown", []) if progress else []
        if breakdown:
            details = Table(box=None, expand=True, padding=(0, 1))
            details.add_column("GROUP")
            details.add_column("SAVED / TOTAL", justify="right")
            details.add_column("PROGRESS", justify="right")
            for group in breakdown:
                details.add_row(
                    str(group.get("label", "—")),
                    (f'{group["saved"]:,} / {group["expected"]:,}' if group["expected"] is not None
                     else f'{group["saved"]:,} / —'),
                    (progress_text(group["saved"], group["expected"], width=8)
                     if group["expected"] is not None else Text("total unknown", style="grey62")),
                )
            notes = Text(
                f'last save {duration(progress.get("last_save_age_seconds"))} ago', style="grey62"
            )
            live_group = next((group for group in breakdown if group.get("rate_per_second")), None)
            if live_group and current["pid"]:
                notes.append(
                    f' · {60 * live_group["rate_per_second"]:.1f} rows/min'
                    f' · current-group ETA ~{duration(live_group.get("eta_seconds"))}',
                    style="bright_cyan",
                )
            else:
                notes.append(" · no cross-group ETA", style="grey50")
            detail_panel = Panel(
                Group(details, notes),
                title=Text(progress.get("breakdown_title", "Breakdown"), style="grey74"),
                border_style="grey35",
                padding=(0, 1),
            )
        else:
            message = current["detail"] if current and current["detail"] else "No detailed measurements for this stage yet."
            detail_panel = Panel(
                Text(message, style="grey70"),
                title=Text("Stage details", style="grey74"),
                border_style="grey35",
            )
        steps_panel = Panel(
            steps,
            title=Text(f"{presentation.entity_name(selected)} · pipeline", style="grey74"),
            border_style="grey35",
            padding=(0, 1),
        )
        if width >= 110:
            columns = Table.grid(expand=True, padding=(0, 1))
            columns.add_column(ratio=1)
            columns.add_column(ratio=1)
            columns.add_row(steps_panel, detail_panel)
            optional: list[RenderableType] = [columns]
        else:
            optional = [steps_panel, detail_panel] if expanded else [detail_panel]
        for panel in optional:
            if expanded or content_height(Group(*parts, panel, *footer), width) <= height:
                parts.append(panel)
    if snapshot.get("recent"):
        recent = snapshot["recent"][-3:]
        for count in range(len(recent), 0, -1):
            panel = Panel(Text("\n".join(recent[-count:]), style="grey74"),
                          title=Text("Recent activity · active process", style="grey74"),
                          border_style="grey35", padding=(0, 1))
            if expanded or content_height(Group(*parts, panel, *footer), width) <= height:
                parts.append(panel)
                break

    parts.extend(footer)
    if expanded:
        parts.append(
            Text(
                "Saved counts come from committed connector records; they are not elapsed-time percentages.\n"
                "Completion markers are monitored; scientific validation remains the experiment's responsibility.",
                style="grey50",
            )
        )
    return Group(*parts)
