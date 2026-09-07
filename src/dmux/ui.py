"""Responsive Rich terminal views for adapter-neutral snapshots."""
from __future__ import annotations

from datetime import datetime, timezone

from .adapters.base import Presentation
from .adapters.supergpqa import SUPERGPQA


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
    capacity = max(1, (width - 14) // 20)
    start = min(max(0, selected_index - capacity // 2), max(0, len(tags) - capacity))
    visible = tags[start : start + capacity]
    tabs = Text("EXPERIMENTS  ", style="bold grey62")
    if start:
        tabs.append("…  ", style="grey50")
    for tag in visible:
        name = presentation.entity_name(tag)
        if len(name) > 15:
            name = name[:14] + "…"
        if tag == selected:
            tabs.append(f" {name} ", style="bold black on bright_cyan")
        else:
            tabs.append(f" {name} ", style="grey74 on grey15")
        tabs.append(" ")
    if start + len(visible) < len(tags):
        tabs.append("…", style="grey50")
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
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    presentation = presentation or SUPERGPQA
    color = COLORS.get(snapshot["state"], "white")
    now = datetime.fromtimestamp(snapshot["updated"], timezone.utc).strftime("%H:%M:%S UTC")
    title = Text("DMUX", style="bold bright_cyan")
    title.append(f"  /  {presentation.name.upper()}", style="bold white")
    title.append(f'    ● {snapshot["state"].upper()}', style=color)
    title.append(f"    {now}", style="grey62")
    parts = [Panel(title, border_style="grey35", box=box.ROUNDED, padding=(0, 1))]
    if "expected" not in snapshot:
        parts += [Text(snapshot["message"], style="yellow"), Text(snapshot["queue"], style="grey62")]
        return Group(*parts)

    summary = Text(
        f'{snapshot["saved"]:,} / {snapshot["expected"]:,} {presentation.unit} saved   ',
        style="bold",
    )
    summary.append_text(
        progress_text(snapshot["saved"], snapshot["expected"], width=16 if width >= 100 else 8)
    )
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
        health.append("   GPU unavailable", style="grey62")
    health.append(
        f'   Disk {snapshot["disk_free_gib"]:.1f} GiB free',
        style="yellow" if snapshot["disk_free_gib"] < 20 else "grey70",
    )
    parts.append(health)

    active = snapshot.get("active")
    if not snapshot["models"]:
        parts.append(Text("No experiments are declared in this plan yet.", style="yellow"))
        parts.append(Text("q quit · r refresh   |   Read-only; jobs keep running.", style="grey58"))
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
        for label in presentation.short_labels:
            overview.add_column(label, justify="center", no_wrap=True)
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
            saved = Text("—", style="grey50")
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
                "needs validation"
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
    current = next((task for task in focus if task["name"] == stage), None) if stage else None
    current = current or next((task for task in focus if task["pid"]), None)
    current = current or next(
        (task for task in focus if task["state"] not in {"complete", "queued"}), None
    )
    current = current or next((task for task in focus if task["state"] != "complete"), None)
    current = current or (focus[-1] if focus else None)
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

    if expanded or height >= 40:
        steps = Table(box=None, expand=True, padding=(0, 1))
        steps.add_column("STAGE")
        steps.add_column("STATE", justify="right")
        steps.add_column("SAVED / TOTAL", justify="right")
        for task in focus:
            count = task["progress"]["saved"] if task["progress"] else 0
            number = (
                f'{count:,} / {task["expected"]:,}'
                if task["expected"] is not None
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
            model = next((model for model in snapshot["models"] if model["tag"] == selected), {})
            steps.add_row("Validation required", Text("blocked", style="yellow"), "—")
            steps.caption = model.get("reason", "Not scheduled")

        progress = current["progress"] if current else None
        breakdown = progress.get("breakdown", progress.get("windows", [])) if progress else []
        if breakdown:
            details = Table(box=None, expand=True, padding=(0, 1))
            details.add_column("GROUP")
            details.add_column("SAVED / TOTAL", justify="right")
            details.add_column("PROGRESS", justify="right")
            for group in breakdown:
                details.add_row(
                    str(group.get("label", group.get("length", "—"))),
                    f'{group["saved"]:,} / {group["expected"]:,}',
                    progress_text(group["saved"], group["expected"], width=8),
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
            parts.append(columns)
        else:
            parts += [steps_panel, detail_panel] if expanded else [detail_panel]
        if (expanded or height >= 48) and snapshot.get("recent"):
            parts.append(
                Panel(
                    Text("\n".join(snapshot["recent"]), style="grey74"),
                    title=Text("Recent activity · active process", style="grey74"),
                    border_style="grey35",
                    padding=(0, 1),
                )
            )

    for warning in snapshot["warnings"][:2]:
        parts.append(Text("! " + warning, style="yellow"))
    if notice:
        parts.append(Text(notice, style="yellow", overflow="ellipsis", no_wrap=True))
    keys = "n/p tabs · Enter details · t tmux · k/K stop · q quit"
    parts.append(
        Text(
            keys + ("   |   Read-only monitoring; stops require confirmation." if width >= 100 else " · read-only monitoring"),
            style="grey58",
        )
    )
    if expanded:
        parts.append(
            Text(
                "Saved counts come from committed connector records; they are not elapsed-time percentages.\n"
                "Completion markers are monitored; scientific validation remains the experiment's responsibility.",
                style="grey50",
            )
        )
    return Group(*parts)
