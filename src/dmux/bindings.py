"""Shared key bindings for dispatch, compact legends, and generated help."""
from __future__ import annotations

from dataclasses import dataclass

from .terminal import DOWN, UP, LEFT, RIGHT
from .mouse import target


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
    Binding(("m",), "m", "m", "Next metrics page when available; does not save results", ("detail",)),
    Binding(("l",), "l", "l", "Open configured log: scroll, search and follow"),
    Binding(("c",), "c", "c", "Compare configured key metrics (explicit bounded reads)"),
    Binding(("o",), "o", "o", "Options; preferences never change experiments", ("dashboard", "detail", "home", "sessions")),
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
    Binding(("j", DOWN), "j/↓", "j", "Scroll down", ("log", "comparison", "options")),
    Binding(("k", UP), "k/↑", "k", "Scroll up", ("log", "comparison", "options")),
    Binding(("\x04", "\x15"), "Ctrl-D/U", "\x04", "Page down / up", ("log",)),
    Binding(("/",), "/", "/", "Search the retained log window", ("log",)),
    Binding(("f",), "f", "f", "Toggle follow mode", ("log",)),
    Binding(("s",), "s", "s", "Cycle comparison sort order", ("comparison",)),
    Binding(("\r", "\n"), "Enter", "\r", "Edit or toggle selected preference", ("options",)),
    Binding((LEFT,), "←", LEFT, "Previous value", ("options",)),
    Binding((RIGHT,), "→", RIGHT, "Next value", ("options",)),
    Binding(("o",), "o", "o", "Options", ("log", "comparison")),
    Binding(("q", "\x1b"), "q/Esc", "q", "Back (options save on close)", ("log", "comparison", "options")),
    Binding(("r", "R"), "r", "r", "Refresh", ("dashboard", "detail", "home", "sessions", "log", "comparison")),
    Binding(("?",), "?", "?", "Show keyboard help", ("dashboard", "detail", "home", "sessions", "log", "comparison", "options")),
)


def action_key(key: str, view: str) -> str:
    return next((binding.action for binding in BINDINGS
                 if view in binding.views and key in binding.keys), key)


def legend(view: str) -> str:
    return " · ".join(binding.label for binding in BINDINGS if view in binding.views)


def render_help(view: str, settings=None):
    from rich.panel import Panel
    from rich.text import Text

    lines = [f"{binding.label:12} {binding.description}" for binding in BINDINGS if view in binding.views]
    from rich.console import Group
    from .appearance import wordmark
    if settings is not None and settings.values['key_style'] != 'both':
        lines = [line.replace('/↓', '').replace('/↑', '') if settings.values['key_style'] == 'letters'
                 else line.replace('n/↓', '↓').replace('p/↑', '↑').replace('j/↓', '↓').replace('k/↑', '↑') for line in lines]
    body = Text()
    bindings = [binding for binding in BINDINGS if view in binding.views]
    for line, binding in zip(lines, bindings):
        # Stop/removal controls remain keyboard-only, including in help.
        safe = not ((view in ('dashboard', 'detail') and binding.action in ('k', 'K'))
                    or (view == 'sessions' and binding.action == 'd'))
        body.append(line + "\n", style=target('key', binding.keys[0]) if safe else None)
    body.append("\nMouse: click tabs/rows/options; wheel scrolls.\n", style='grey70')
    body.append("Esc closes help", style=target('close_help', '', 'cyan'))
    return Panel(Group(wordmark(settings, context='HELP', compact=True), body),
                 title="DMUX / KEYBOARD HELP", border_style="cyan")



def markdown() -> str:
    lines = ["# Keyboard and mouse controls", "", "Generated from `dmux.bindings.BINDINGS`.", "",
             "Press or click `? help` for controls. Click a tab to select it, an experiment row to",
             "open details, or a stage row to inspect it. Home rows/recent tabs open details;",
             "session rows open the selected tmux destination. Click a preference to edit/toggle it.",
             "The wheel navigates lists/tabs and scrolls logs or comparisons. Help entries for",
             "navigation and options are clickable; stop/removal confirmation remains typed.",
             "", "Mouse reporting is enabled only in interactive terminals, using",
             "[SGR mouse reporting](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html#h3-Extended-coordinates).",
             "Use `--no-mouse`, `DMUX_MOUSE=false`, or `dmux settings set mouse false` to disable it.",
             "Keyboard controls remain available; terminal/emulator shortcuts can bypass mouse",
             "reporting for text selection. dmux does not change your tmux server settings.", ""]
    for view in ("dashboard", "detail", "home", "sessions", "log", "comparison", "options"):
        lines += [f"## {view.title()}", "", "| Keys | Action |", "| --- | --- |"]
        lines += [f"| {binding.label} | {binding.description} |" for binding in BINDINGS if view in binding.views]
        lines.append("")
    return "\n".join(lines)
