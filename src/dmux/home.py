"""Searchable global home screen. Registration is explicit; monitoring is read-only."""
from __future__ import annotations

from collections import Counter
import json
import time

from .registry import load_adapter
from .catalog import ProjectCatalog, CatalogError, atomic_json, user_directory
from .monitor import Monitor
from .connectors import open_regular
from .system import HostSampler
from .bindings import action_key, render_help
from .snapshots import export_home


def run_state(tasks):
    if any(t["state"] in {"failed", "invalid"} for t in tasks):
        return "needs attention"
    if any(t["pid"] for t in tasks):
        return "running"
    if tasks and all(t["state"] == "complete" for t in tasks):
        return "complete"
    for state in ("scheduler running", "scheduled", "unavailable"):
        if any(t["state"] == state for t in tasks):
            return state
    if any(t["state"] in {"partial", "interrupted"} for t in tasks):
        return "interrupted"
    return "idle"


class Home:
    def __init__(self, catalog=None, *, sampler=None, clock=time.monotonic, background=False):
        self.catalog = catalog or ProjectCatalog()
        self.sampler = sampler or HostSampler()
        self.clock = clock
        self.entries = []
        self.monitors, self.snapshots, self.due = {}, {}, {}
        self.query, self.filter = "", "all"
        self.selected, self.notice = None, None
        self.registry_time = -float("inf")
        self.cursor = 0
        self.opened = []
        self.catalog_error = None
        self.background = background

    def refresh(self, *, force=False):
        now = self.clock()
        if force or now - self.registry_time >= 2:
            try:
                entries = self.catalog.read()
                for entry in entries:
                    if entry not in self.entries:
                        self.monitors.pop(entry["id"], None)
                        self.due[entry["id"]] = -float("inf")
                live_ids = {e["id"] for e in entries}
                for mapping in (self.monitors, self.snapshots, self.due):
                    for key in list(mapping):
                        if key not in live_ids:
                            del mapping[key]
                self.entries = entries
                if self.notice == self.catalog_error:
                    self.notice = None
                self.catalog_error = None
            except CatalogError as exc:
                self.notice = self.catalog_error = str(exc)
            self.registry_time = now
        # Rotate a bounded number of project refreshes so large lists stay usable.
        count, refreshed = len(self.entries), 0
        for _ in range(count):
            entry = self.entries[self.cursor % count]
            self.cursor += 1
            key = entry["id"]
            if not force and now < self.due.get(key, 0):
                continue
            try:
                if key not in self.monitors:
                    adapter = load_adapter(entry.get("adapter", "filesystem"))
                    if configure := getattr(adapter, "configure_observation", None):
                        configure(background=self.background)
                    self.monitors[key] = Monitor(entry["plan_dir"], project_root=entry["project_root"],
                        results_dir=entry.get("results_dir"), adapter=adapter,
                        processes=lambda a=adapter: self.sampler.processes(a), gpu=self.sampler.gpu)
                snapshot = self.monitors[key].snapshot()
                if snapshot["state"] == "waiting":
                    snapshot = {**snapshot, "state": "unavailable"}
            except Exception as exc:
                snapshot = {"state": "unavailable", "message": str(exc), "tasks": [], "experiments": []}
            self.snapshots[key] = snapshot
            self.due[key] = now + (30 if snapshot["state"] == "complete" else 2)
            refreshed += 1
            if not force and refreshed >= 4:
                break

    def rows(self):
        rows = []
        for entry in self.entries:
            snapshot = self.snapshots.get(entry["id"], {})
            runs = snapshot.get("experiments", [])
            if not runs:
                rows.append({"id": entry["id"], "project": entry, "experiment": None,
                    "label": snapshot.get("message", "No experiments declared" if snapshot else "Waiting for refresh"),
                    "state": snapshot.get("state", "loading"), "stages": "—", "pids": [],
                    "updated": snapshot.get("updated")})
            for run in runs:
                tasks = [t for t in snapshot["tasks"] if t["experiment"] == run["tag"]]
                rows.append({"id": entry["id"] + "/" + run["tag"], "project": entry,
                    "experiment": run["tag"], "label": run["label"], "state": run_state(tasks),
                    "stages": f'{run["completed_stages"]}/{run["stages"]}',
                    "pids": sorted({p["pid"] for t in tasks for p in t.get("processes", [])}),
                    "updated": snapshot.get("updated")})
        priority = {"needs attention": 0, "running": 1, "scheduler running": 1, "scheduled": 2,
                    "interrupted": 2, "unavailable": 3, "invalid": 3, "idle": 4, "complete": 5}
        rows.sort(key=lambda row: (row["project"]["name"].casefold(), priority.get(row["state"], 4), row["label"]))
        return rows

    def visible(self):
        return [r for r in self.rows() if
            self.query.casefold() in (r["project"]["name"] + " " + r["label"] + " " + (r["experiment"] or "")).casefold()
            and (self.filter == "all" or self.filter == "running" and (r["pids"] or r["state"] == "scheduler running") or
                 self.filter == "attention" and r["state"] in {"needs attention", "unavailable", "invalid", "interrupted"})]

    def current(self):
        rows = self.visible()
        return next((r for r in rows if r["id"] == self.selected), next(iter(rows), None))

    def move(self, direction):
        rows, current = self.visible(), self.current()
        if current:
            self.selected = rows[(rows.index(current) + direction) % len(rows)]["id"]

    def render(self, *, height=40, searching=False):
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        rows = self.visible()
        current = self.current()
        all_rows = self.rows()
        counts = Counter(r["state"] for r in all_rows)
        running = sum(bool(r["pids"]) for r in all_rows)
        scheduled = counts["scheduler running"] + counts["scheduled"]
        scheduler_text = f" · {scheduled} scheduler active" if scheduled else ""
        parts = [Text("DMUX / ALL EXPERIMENTS", style="bold bright_cyan"),
            Text(f'{len(self.entries)} {"project" if len(self.entries) == 1 else "projects"} · {running} running{scheduler_text} · {counts["needs attention"]} need attention · {counts["complete"]} complete', style="grey74"),
            Text(f'Filter: {self.filter}   Search: {self.query}' + ("▏" if searching else ""), style="cyan")]
        opened_rows = {r["id"]: r for r in self.rows()}
        tabs = Text("OPEN  ", style="grey62", overflow="ellipsis", no_wrap=True)
        for key in self.opened:
            if key in opened_rows:
                row = opened_rows[key]
                tabs.append(f' {row["project"]["name"]}/{row["label"]} ',
                            style="bold black on cyan" if current and key == current["id"] else "cyan")
        if self.opened:
            parts.append(tabs)
        table = Table(box=box.SIMPLE_HEAD, expand=True, padding=(0, 1))
        for heading in ("PROJECT", "EXPERIMENT", "STATE", "STAGES", "PID"):
            table.add_column(heading, overflow="ellipsis", no_wrap=True)
        limit = max(1, height - 16)
        index = rows.index(current) if current else 0
        start = max(0, min(index - limit // 2, len(rows) - limit))
        for row in rows[start:start + limit]:
            selected = current is not None and row["id"] == current["id"]
            color = ("red" if row["state"] in {"needs attention", "invalid"} else
                     "cyan" if row["pids"] else "green" if row["state"] == "complete" else "yellow")
            table.add_row(Text(("› " if selected else "  ") + row["project"]["name"], style="bold white" if selected else "grey70"),
                Text(row["label"]), Text(row["state"], style=color), Text(row["stages"]),
                Text(",".join(str(p) for p in row["pids"]) or "—"))
        parts.append(table)
        if not self.entries:
            parts.append(Text("No registered projects. Use dmux add /path/to/project.\nTry dmux demo --live for a small tour.", style="yellow"))
        elif not rows:
            parts.append(Text("No matches. Change the search or press f to switch filters.", style="yellow"))
        if current:
            parts.append(Text(current["project"]["plan_dir"], style="grey62", overflow="ellipsis", no_wrap=True))
            age = max(0, time.time() - current["updated"]) if current["updated"] else None
            parts.append(Text(f'Last checked {age:.0f}s ago · r refresh' if age is not None else "Not checked yet · r refresh", style="grey62"))
        if self.notice:
            parts.append(Text(self.notice, style="yellow", overflow="ellipsis", no_wrap=True))
        parts.append(Text(f'{len(rows)} matching entries · stage states only; no cross-project percentage', style="grey62"))
        parts.append(Text("j/k select · / search · f filter · Enter inspect · t tmux · q quit · ? help", style="grey70"))
        parts.append(Text("Tab recent experiment · x close recent tab (jobs continue)", style="grey70"))
        parts.append(Text("Registration never starts/stops jobs. Result plots load only inside an experiment.", style="grey62"))
        return Panel(Group(*parts), border_style="grey35")


def main(argv=None):
    import argparse
    from rich.console import Console
    from rich.live import Live
    from .terminal import keyboard

    parser = argparse.ArgumentParser(prog="dmux home", description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--schema-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--no-gpu", action="store_true")
    args = parser.parse_args(argv)
    console = Console(highlight=False)
    background = console.is_terminal and not (args.once or args.json)
    home = Home(sampler=HostSampler(background=background), background=background)
    if args.no_gpu:
        home.sampler.gpu = lambda: {"devices": [], "error": "disabled"}
    state_path = user_directory("state") / "home.json"
    if not args.json and not args.once and console.is_terminal:
        try:
            with open_regular(state_path) as handle:
                state = json.loads(handle.read(16_384))
            home.selected = state.get("selected")
            opened = state.get("opened", [])
            if isinstance(opened, list) and all(isinstance(key, str) for key in opened):
                home.opened = opened[-8:]
        except (OSError, ValueError, AttributeError):
            pass
    home.refresh(force=args.once or args.json or not console.is_terminal)
    if args.json:
        print(json.dumps(export_home(home.entries, home.rows(), home.notice, version=args.schema_version),
                         indent=2, allow_nan=False))
        if home.catalog_error:
            raise SystemExit(2)
        return
    if args.once or not console.is_terminal:
        console.print(home.render(height=console.height))
        if home.catalog_error:
            raise SystemExit(2)
        return
    running, searching, helping = True, False, False
    try:
        while running:
            action = None
            with keyboard() as read_keys, Live(console=console, screen=True, auto_refresh=False, vertical_overflow="crop") as live:
                previous, updated = None, -float("inf")
                while running and action is None:
                    redraw = False
                    for key in read_keys():
                        if not searching and not helping and key != "t":
                            key = action_key(key, "home")
                        redraw = True
                        if key == "\x03":
                            running = False
                        elif helping:
                            if key in ("?", "q", "Q", "\x1b"):
                                helping = False
                        elif searching:
                            if key in "\r\n\x1b":
                                searching = False
                            elif key in "\b\x7f":
                                home.query = home.query[:-1]
                            elif key.isprintable() and len(home.query) < 100:
                                home.query += key
                        elif key == "?":
                            helping = True
                        elif key in "qQ":
                            running = False
                        elif key in "jk":
                            home.move(1 if key == "j" else -1)
                        elif key == "/":
                            searching, home.query = True, ""
                        elif key == "f":
                            modes = ("all", "running", "attention")
                            home.filter = modes[(modes.index(home.filter) + 1) % len(modes)]
                        elif key == "\t" and home.opened:
                            available = {r["id"] for r in home.rows()}
                            tabs = [key for key in home.opened if key in available]
                            if tabs:
                                index = tabs.index(home.selected) if home.selected in tabs else -1
                                home.selected = tabs[(index + 1) % len(tabs)]
                                home.query, home.filter = "", "all"
                        elif key == "x" and home.current():
                            home.opened = [key for key in home.opened if key != home.current()["id"]]
                            home.notice = "Recent tab closed. Registration, jobs and result files are unchanged."
                        elif key == "r":
                            home.due = dict.fromkeys(home.due, -float("inf"))
                            home.registry_time = updated = -float("inf")
                        elif key in "\r\nt" and home.current():
                            row = home.current()
                            home.selected = row["id"]
                            if row["experiment"] or key == "t":
                                if row["experiment"] and row["id"] not in home.opened:
                                    home.opened = [*home.opened, row["id"]][-8:]
                                action = ("tmux" if key == "t" else "open", row)
                                break
                            home.notice = "Project unavailable or empty. Check its path with dmux doctor --plan-dir PATH."
                    now = time.monotonic()
                    if now - updated >= .5:
                        home.refresh()
                        updated, redraw = now, True
                    if redraw or console.size != previous:
                        live.update(render_help("home") if helping else home.render(height=console.height, searching=searching), refresh=True)
                        previous = console.size
                    if running and action is None:
                        time.sleep(.1)
            # Restore screen and termios before invoking a project view or tmux.
            if action:
                kind, row = action
                entry = row["project"]
                from .cli import main as watch

                flags = ["watch", "--plan-dir", entry["plan_dir"], "--project-root", entry["project_root"],
                         "--adapter", entry.get("adapter", "filesystem")]
                if entry.get("results_dir"):
                    flags += ["--results-dir", entry["results_dir"]]
                if row["experiment"]:
                    flags += ["--experiment", row["experiment"]]
                if args.no_gpu:
                    flags.append("--no-gpu")
                try:
                    watch(flags, _entry="tmux" if kind == "tmux" else "detail", _return_home=True)
                except (OSError, ValueError, SystemExit) as exc:
                    from .terminal import TerminalSignal

                    if isinstance(exc, TerminalSignal):
                        raise
                    home.notice = f"Could not open project: {exc}"
                home.due[entry["id"]] = -float("inf")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            atomic_json(state_path, {"version": 1, "selected": home.selected, "opened": home.opened})
        except (OSError, CatalogError):
            pass  # Optional view state must never interfere with terminal restoration.
