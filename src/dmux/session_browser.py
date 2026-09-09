"""Searchable session/window browser shared by the dashboard and standalone CLI."""
from __future__ import annotations

import json
import time

from .sessions import SessionError, SessionManager
from .terminal import keyboard
from .bindings import action_key, render_help
from .refresh import BackgroundRefresh
from .tmux import clean


class SessionBrowser:
    def __init__(self, navigator, tasks=(), *, preferred=None, sessions=True, background=False):
        self.navigator = navigator
        self.manager = SessionManager(navigator)
        self.tasks = list(tasks)
        self.preferred = preferred
        self.sessions = sessions
        self.index = 0
        self.query = ""
        self.searching = False
        self.removal = None
        self.confirmation = ""
        self.notice = None
        self.data = {"panes": [], "associations": {}, "inside": False, "error": None}
        self.window_names = {}
        self.locations = {}
        self.help = False
        self.worker = BackgroundRefresh(self._read) if background else None
        self.refresh()
        preferred_pane = self.data["associations"].get(preferred, {}).get("pane")
        if preferred_pane:
            self.index = next((i for i, p in enumerate(self.rows)
                               if p["session_id"] == preferred_pane["session_id"]
                               and (sessions or p["pane_id"] == preferred_pane["pane_id"])), 0)

    @property
    def rows(self):
        panes = self.data["panes"]
        query = self.query.casefold()
        matches = [p for p in panes if query in " ".join((p["session"], p["command"],
                    self.window_names.get(p["window_id"], ""), p["label"])).casefold()]
        if not self.sessions:
            return matches
        grouped = {}
        for pane in matches:
            current = grouped.get(pane["session_id"])
            if current is None or pane["active"]:
                grouped[pane["session_id"]] = pane
        return list(grouped.values())

    def _read(self):
        data = self.navigator.snapshot(self.tasks)
        output, _ = self.navigator.read(["list-windows", "-a", "-F", "#{window_id}\t#{window_name}"])
        names = {parts[0]: clean(parts[1]) for line in output.splitlines()
                 if len(parts := line.split("\t")) == 2}
        output, _ = self.navigator.read(["list-sessions", "-F",
                                         "#{session_id}\t#{@dmux-project-root}\t#{@dmux-results-dir}"])
        locations = {parts[0]: (clean(parts[1]), clean(parts[2])) for line in output.splitlines()
                     if len(parts := line.split("\t")) == 3}
        return data, names, locations

    def _apply(self, result):
        rows = self.rows
        previous = rows[min(self.index, len(rows) - 1)]["target"] if rows else None
        self.data, self.window_names, self.locations = result
        preferred = self.data["associations"].get(self.preferred, {}).get("pane")
        self.index = next((i for i, p in enumerate(self.rows) if p["target"] == previous),
                          next((i for i, p in enumerate(self.rows) if preferred
                                and p["session_id"] == preferred["session_id"]
                                and (self.sessions or p["pane_id"] == preferred["pane_id"])), 0))

    def refresh(self):
        if self.worker is not None:
            self.worker.request()
        else:
            self._apply(self._read())

    def poll(self):
        result = self.worker.take() if self.worker is not None else None
        if result is None:
            return False
        value, error = result
        if error is not None:
            self.notice = f"Refresh unavailable: {error}"
        else:
            self._apply(value)
        return True

    def key(self, key):
        """Return ('open', pane), ('close', None), or None."""
        if key == "\x03":
            return "close", None
        if self.removal is not None:
            if key == "\x1b":
                self.removal, self.confirmation = None, ""
            elif key in "\r\n":
                try:
                    self.notice = self.manager.remove(self.removal, confirmation=self.confirmation)
                except SessionError as exc:
                    self.notice = str(exc)
                self.removal, self.confirmation = None, ""
                self.refresh()
            elif key in "\x7f\b":
                self.confirmation = self.confirmation[:-1]
            elif key.isprintable():
                self.confirmation += key
            return None
        if self.searching:
            if key in "\r\n":
                self.searching = False
            elif key == "\x1b":
                self.searching, self.query = False, ""
            elif key in "\x7f\b":
                self.query = self.query[:-1]
            elif key.isprintable():
                self.query += key
            self.index = 0
            return None
        if self.help:
            if key in ("?", "q", "Q", "\x1b"):
                self.help = False
            return None
        key = action_key(key, "sessions")
        if key == "?":
            self.help = True
            return None
        rows = self.rows
        if key in "qQ\x1b":
            return "close", None
        if key in "jJnNkKpP" and rows:
            self.index = (self.index + (1 if key in "jJnN" else -1)) % len(rows)
        elif key in "\r\n" and rows:
            return "open", rows[self.index]
        elif key in "sS\t":
            self.sessions = not self.sessions
            self.index = 0
        elif key == "/":
            self.searching = True
        elif key in "rR":
            self.refresh()
        elif key in "dD" and rows:
            try:
                self.removal = self.manager.prepare_removal(rows[self.index])
                self.confirmation = ""
                self.notice = None
            except SessionError as exc:
                self.notice = str(exc)
        return None

    def render(self, *, height=24):
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        if self.help:
            return render_help("sessions")
        heading = Text("TMUX  /  Loading sessions…" if self.worker and self.worker.pending and not self.data["panes"]
                       else "TMUX  /  SESSION & PANE PICKER", style="bold bright_cyan")
        if self.removal:
            removal = self.removal
            details = Text(f'Remove session "{removal.name}"?\n', style="bold red")
            details.append(f"All {len(removal.panes)} panes will close, terminating jobs and AI chats.\n")
            details.append("Result files stay on disk.\n", style="grey74")
            for pane in removal.panes[:max(1, height - 13)]:
                details.append(f'  {pane["label"]} · {pane["command"]}\n', style="grey74")
            details.append(f"Type {removal.name} and press Enter: ", style="yellow")
            details.append(self.confirmation + "▏", style="bold white")
            return Panel(Group(heading, details, Text("Esc cancels removal", style="grey70")),
                         border_style="red", box=box.ROUNDED)

        rows = self.rows
        self.index = min(self.index, max(0, len(rows) - 1))
        table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
        table.add_column("SESSION" if self.sessions else "SESSION / WINDOW.PANE")
        table.add_column("WINDOWS" if self.sessions else "COMMAND")
        table.add_column("EXPERIMENT")
        page_size = max(1, height - 14)
        start = self.index // page_size * page_size
        for index in range(start, min(start + page_size, len(rows))):
            pane = rows[index]
            others = [p for p in self.data["panes"] if p["session_id"] == pane["session_id"]]
            windows = dict.fromkeys(self.window_names.get(p["window_id"], str(p["window"])) for p in others)
            label = pane["session"] if self.sessions else (
                f'{pane["session"]} / {self.window_names.get(pane["window_id"], str(pane["window"]))}.{pane["pane"]}'
            )
            related = [tag for tag, link in self.data["associations"].items()
                       if link.get("pane") and link["pane"]["session_id"] == pane["session_id"]]
            table.add_row(Text(("› " if index == self.index else "  ") + label,
                               style="bold bright_cyan" if index == self.index else "grey74"),
                          Text(", ".join(windows) if self.sessions else pane["command"]),
                          Text(", ".join(related), style="grey62"))
        parts = [heading, Text("Sessions" if self.sessions else "Windows & panes", style="grey74")]
        if self.query or self.searching:
            parts.append(Text("Search: " + self.query + ("▏" if self.searching else ""), style="bright_cyan"))
        if rows:
            parts.append(table)
        else:
            parts.append(Text(self.data.get("error") or "No matching sessions.", style="yellow"))
        parts.append(Text(f"{self.index + 1 if rows else 0}/{len(rows)} · Enter open · detach to return", style="grey62"))
        if rows:
            root, results = self.locations.get(rows[self.index]["session_id"], ("", ""))
            if root:
                parts.append(Text("Project: " + root, style="grey62", no_wrap=True, overflow="ellipsis"))
            if results:
                parts.append(Text("Results: " + results, style="grey62", no_wrap=True, overflow="ellipsis"))
        if self.notice:
            parts.append(Text(self.notice, style="yellow", overflow="ellipsis", no_wrap=True))
        parts.append(Text("↑/↓ j/k select · / search · Tab windows/sessions · d remove", style="grey70"))
        parts.append(Text("r refresh · Esc/q back · ? help", style="grey70"))
        return Panel(Group(*parts), border_style="grey35", box=box.ROUNDED)


def browse(navigator, *, json_output=False):
    from rich.console import Console
    from rich.live import Live

    console = Console(highlight=False)
    browser = SessionBrowser(navigator, background=console.is_terminal and not json_output)
    if json_output:
        print(json.dumps(browser.data, indent=2))
        return
    if not console.is_terminal:
        console.print(browser.render(height=console.height))
        return
    running = True
    try:
        while running:
            selected = None
            with keyboard() as keys, Live(console=console, screen=True, auto_refresh=False) as live:
                previous_size = None
                while running and selected is None:
                    pressed = keys()
                    for key in pressed:
                        action = browser.key(key)
                        if action:
                            if action[0] == "open":
                                selected = action[1]
                            else:
                                running = False
                            break
                    refreshed = browser.poll()
                    if pressed or refreshed or console.size != previous_size:
                        live.update(browser.render(height=console.height), refresh=True)
                        previous_size = console.size
                    if running and selected is None:
                        time.sleep(.1)
            if running and selected:
                browser.notice = navigator.open(selected)
                browser.refresh()
    except KeyboardInterrupt:
        pass
