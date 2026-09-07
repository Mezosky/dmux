"""Optional tmux navigation; discovery is read-only, switching needs a keypress.

Never creates sessions, sends input to panes, detaches other clients, or changes
experiment processes. Targets are revalidated numeric IDs, never shell commands.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

PANE_FORMAT = "\t".join("#{" + name + "}" for name in (
    "session_id", "window_id", "pane_id", "pane_pid", "session_name",
    "window_index", "pane_index", "window_active", "pane_active", "pane_current_command"))


def clean(text):
    return "".join(c for c in str(text) if c.isprintable())[:160]


def parse_panes(text):
    panes = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != 10:
            continue
        sid, wid, pid, process, name, window, pane, wa, pa, command = fields
        if not all(re.fullmatch(pattern, value) for pattern, value in (
            (r"\$\d+", sid), (r"@\d+", wid), (r"%\d+", pid),
            (r"[1-9]\d*", process), (r"\d+", window), (r"\d+", pane))):
            continue
        panes.append({"session_id": sid, "window_id": wid, "pane_id": pid,
            "pane_pid": int(process), "session": clean(name), "window": int(window),
            "pane": int(pane), "active": wa == "1" and pa == "1", "command": clean(command),
            "label": clean(f"{name}:{window}.{pane}"), "target": f"{sid}:{wid}.{pid}"})
    return sorted(panes, key=lambda p: (p["session"], p["window"], p["pane"]))


def parse_links(values):
    links = {}
    for value in values:
        model, sep, target = value.partition("=")
        if not sep or not model or not target or model in links:
            raise ValueError("Use unique --tmux-link MODEL=SESSION[:WINDOW.PANE] entries")
        links[model] = target
    return links


def ancestors(pid):
    import psutil
    try:
        process = psutil.Process(pid)
        return [pid, *(p.pid for p in process.parents())]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def associate(panes, tasks, links, parent_lookup=ancestors):
    """Only a live PID ancestry establishes ownership; explicit links are labeled."""
    result = {}
    models = dict.fromkeys([*(t["model"] for t in tasks), *(m for m in links if m != "*")])
    for model in models:
        target = links.get(model, links.get("*"))
        if target is not None:
            matches = [p for p in panes if target in
                       (p["session"], p["session_id"], p["pane_id"], p["label"], p["target"])]
            if matches:
                result[model] = {"pane": next((p for p in matches if p["active"]), matches[0]),
                                 "source": "explicit link"}
            else:
                result[model] = {"pane": None, "source": "missing explicit target", "target": clean(target)}
            continue
        for task in tasks:
            if task["model"] != model or not task.get("pid"):
                continue
            chain = parent_lookup(task["pid"])
            matches = [p for p in panes if p["pane_pid"] in chain]
            if matches:
                match = min(matches, key=lambda p: chain.index(p["pane_pid"]))
                result[model] = {"pane": match, "source": "process ancestry"}
                break
    return result


class TmuxNavigator:
    def __init__(self, *, socket=None, links=None, client=None, run=subprocess.run, env=None):
        self.env = dict(os.environ if env is None else env)
        self.socket = str(Path(socket).resolve()) if socket else None
        self.links, self.client, self.run = links or {}, client, run
        # -N also prevents attach-session from starting a server after a race.
        self.command = ["tmux", "-N", *(["-S", self.socket] if self.socket else [])]

    def read(self, args):
        try:
            out = self.run([*self.command, *args], capture_output=True, text=True,
                           timeout=2, check=False, env=self.env)
        except FileNotFoundError:
            return "", "tmux is not installed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", f"tmux unavailable ({type(exc).__name__})"
        if out.returncode:
            return "", clean(out.stderr or "tmux query failed")
        return out.stdout, None

    def snapshot(self, tasks):
        output, error = self.read(["list-panes", "-a", "-F", PANE_FORMAT])
        panes = parse_panes(output)
        return {"panes": panes, "associations": associate(panes, tasks, self.links),
                "error": error, "inside": bool(self.env.get("TMUX"))}

    def open(self, selected):
        """Call only after explicit selection and restoring Live/terminal modes."""
        latest = self.snapshot([])
        target = next((p for p in latest["panes"] if
            (p["session_id"], p["window_id"], p["pane_id"], p["pane_pid"]) ==
            (selected["session_id"], selected["window_id"], selected["pane_id"], selected["pane_pid"])), None)
        if target is None:
            return "tmux target disappeared; refresh the picker. Jobs were not changed."
        if self.env.get("TMUX"):
            own_socket = self.env["TMUX"].rsplit(",", 2)[0]
            if self.socket and Path(own_socket).resolve() != Path(self.socket):
                return "Cross-server switching is disabled inside tmux; open this dashboard outside tmux."
            own_panes = [p for p in latest["panes"] if p["pane_id"] == self.env.get("TMUX_PANE")]
            if not own_panes:
                return "Cannot identify the dashboard's tmux pane; no client switched."
            clients, error = self.read(["list-clients", "-F", "#{session_id}\t#{client_tty}"])
            if error:
                return error
            sessions = {p["session_id"] for p in own_panes}
            candidates = sorted({parts[1] for line in clients.splitlines()
                if len(parts := line.split("\t")) == 2 and parts[0] in sessions
                and re.fullmatch(r"/dev/[A-Za-z0-9_./-]+", parts[1])})
            if self.client:
                candidates = [c for c in candidates if c == self.client]
            if len(candidates) != 1:
                return "Cannot choose a unique client; use --tmux-client /dev/pts/N (no client switched)."
            _, error = self.read(["switch-client", "-E", "-c", candidates[0], "-t", target["target"]])
            return error or f'Switched to {target["label"]}; return with your tmux session/window switcher.'
        try:
            # Inherit the real terminal. No -d/-x: other clients stay attached.
            out = self.run([*self.command, "attach-session", "-E", "-t", target["target"]],
                           check=False, env=self.env)
        except (OSError, KeyboardInterrupt) as exc:
            return f"tmux attachment ended ({type(exc).__name__}); jobs were not changed."
        return "Returned from tmux; experiments keep running." if out.returncode == 0 else "tmux attachment failed; experiments were not changed."


def render_picker(tmux, *, index=0, height=24, preferred=None):
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    panes = tmux["panes"]
    heading = Text("TMUX  /  SESSION & PANE PICKER", style="bold bright_cyan")
    if not panes:
        body = Text("No tmux panes available on this socket.\n", style="yellow")
        if tmux.get("error"):
            body.append(clean(tmux["error"]) + "\n", style="grey62")
        body.append("No sessions will be created and no jobs will be moved.", style="grey74")
    else:
        body = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
        body.add_column("SESSION:WINDOW.PANE")
        body.add_column("COMMAND")
        body.add_column("LINK", no_wrap=True)
        page_size = max(1, height - 9)
        start = (index // page_size) * page_size
        for i in range(start, min(start + page_size, len(panes))):
            pane = panes[i]
            related = [tag for tag, link in tmux["associations"].items()
                       if link.get("pane") and link["pane"]["target"] == pane["target"]]
            body.add_row(Text(("› " if i == index else "  ") + pane["label"],
                              style="bold bright_cyan" if i == index else "grey74"),
                         Text(pane["command"], style="grey70"),
                         Text(", ".join(related), style="bright_cyan" if preferred in related else "grey62"))
        body.caption = f"{index + 1}/{len(panes)} panes · " + ("switch current client" if tmux["inside"] else "attach; detach to return here")
    return Panel(Group(heading, body, Text("j/k or ↑/↓ select · Enter open · r refresh · Esc/q back", style="grey70")),
                 border_style="grey35", box=box.ROUNDED)
