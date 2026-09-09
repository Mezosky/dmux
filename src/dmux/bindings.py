"""Shared key bindings for dispatch, compact legends, and generated help."""
from __future__ import annotations

from dataclasses import dataclass

from .terminal import DOWN, UP


@dataclass(frozen=True)
class Binding:
    keys: tuple[str, ...]
    label: str
    action: str
    description: str
    views: tuple[str, ...] = ("dashboard", "detail")


BINDINGS = (
    Binding(("n", "N", DOWN), "n/↓", "n", "Next experiment tab"),
    Binding(("p", "P", UP), "p/↑", "p", "Previous experiment tab"),
    Binding(("\r", "\n"), "Enter", "\r", "Open experiment details", ("dashboard",)),
    Binding(("[",), "[", "[", "Previous stage"),
    Binding(("]",), "]", "]", "Next stage"),
    Binding(("a", "A"), "a", "a", "Follow the active experiment and stage"),
    Binding(("t", "T"), "t", "t", "Open tmux session browser"),
    Binding(("k",), "k", "k", "Review stop for selected stage; exact label required"),
    Binding(("K",), "K", "K", "Review stop for experiment; exact label required"),
    Binding(("x", "X"), "x", "x", "Hide experiment tab; jobs keep running"),
    Binding(("u", "U"), "u", "u", "Restore hidden experiment tabs"),
    Binding(("m",), "m", "m", "Page result metrics", ("detail",)),
    Binding(("r", "R"), "r", "r", "Refresh", ("dashboard", "detail", "home", "sessions")),
    Binding(("?",), "?", "?", "Show keyboard help", ("dashboard", "detail", "home", "sessions")),
    Binding(("q", "Q", "\x1b"), "q/Esc", "q", "Back from details; q quits dashboard"),
    Binding(("j", DOWN), "j/↓", "j", "Next run", ("home",)),
    Binding(("k", UP), "k/↑", "k", "Previous run", ("home",)),
    Binding(("/",), "/", "/", "Search", ("home", "sessions")),
    Binding(("f",), "f", "f", "Cycle all / running / attention", ("home",)),
    Binding(("\t",), "Tab", "\t", "Select a recent experiment", ("home",)),
    Binding(("x",), "x", "x", "Close recent tab only", ("home",)),
    Binding(("\r", "\n", "t"), "Enter / t", "\r", "Open details / tmux", ("home",)),
    Binding(("q", "Q"), "q", "q", "Quit home", ("home",)),
    Binding(("j", "J", "n", "N", DOWN), "j/n/↓", "j", "Next session or pane", ("sessions",)),
    Binding(("k", "K", "p", "P", UP), "k/p/↑", "k", "Previous session or pane", ("sessions",)),
    Binding(("\r", "\n"), "Enter", "\r", "Open selected session or pane", ("sessions",)),
    Binding(("s", "S", "\t"), "s/Tab", "s", "Toggle sessions / panes", ("sessions",)),
    Binding(("d", "D"), "d", "d", "Review session removal; exact name required", ("sessions",)),
    Binding(("q", "Q", "\x1b"), "q/Esc", "q", "Close browser", ("sessions",)),
)


def action_key(key: str, view: str) -> str:
    return next((binding.action for binding in BINDINGS
                 if view in binding.views and key in binding.keys), key)


def legend(view: str) -> str:
    return " · ".join(binding.label for binding in BINDINGS if view in binding.views)


def render_help(view: str):
    from rich.panel import Panel
    from rich.text import Text

    lines = [f"{binding.label:12} {binding.description}" for binding in BINDINGS if view in binding.views]
    return Panel(Text("\n".join([*lines, "", "?/Esc/q closes help · Ctrl-C quits"])),
                 title="DMUX / KEYBOARD HELP", border_style="cyan")


def markdown() -> str:
    lines = ["# Keyboard controls", "", "Generated from `dmux.bindings.BINDINGS`.", ""]
    for view in ("dashboard", "detail", "home", "sessions"):
        lines += [f"## {view.title()}", "", "| Keys | Action |", "| --- | --- |"]
        lines += [f"| {binding.label} | {binding.description} |" for binding in BINDINGS if view in binding.views]
        lines.append("")
    return "\n".join(lines)
