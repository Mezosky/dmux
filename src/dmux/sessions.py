"""Explicit tmux workspace creation and confirmed session removal.

The monitor never calls these operations during polling. Creation and removal
are separate user actions; removal closes the session's jobs and chat windows.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

from .tmux import TmuxNavigator, clean


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Removal:
    session_id: str
    name: str
    fingerprint: tuple
    panes: tuple


class SessionManager:
    def __init__(self, navigator: TmuxNavigator):
        self.navigator = navigator

    def _run(self, arguments, *, allow_server_start=False):
        nav = self.navigator
        prefix = (["tmux", *(["-S", nav.socket] if nav.socket else [])]
                  if allow_server_start else nav.command)
        try:
            result = nav.run([*prefix, *arguments], capture_output=True, text=True,
                             timeout=5, check=False, env=nav.env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SessionError(f"tmux unavailable: {exc}") from exc
        if result.returncode:
            raise SessionError(clean(result.stderr or "tmux command failed"))
        return result.stdout.strip()

    @staticmethod
    def _launcher(command):
        payload = base64.urlsafe_b64encode(json.dumps(list(command)).encode()).decode()
        return [sys.executable, str(Path(__file__).with_name("session_exec.py")), payload]

    def create(self, name, *, project_root, results_dir=None, command=()):
        """Create one detached chat workspace; never reuse/overwrite a session."""
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", name):
            raise SessionError("Session names must use letters, digits, underscores or hyphens (max 64)")
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise SessionError(f"Project directory does not exist: {root}")
        results = Path(results_dir).expanduser() if results_dir is not None else root
        if not results.is_absolute():
            results = root / results
        results = results.resolve()
        # Launch through an argv-only helper: even a single executable passed to
        # tmux would otherwise be interpreted as a shell command string.
        argv = list(command) or [self.navigator.env.get("SHELL", "/bin/sh"), "-i"]
        launcher = self._launcher(argv)
        sid = self._run(["new-session", "-d", "-P", "-F", "#{session_id}", "-s", name,
                         "-n", "chat", "-c", str(root),
                         "-e", f"DMUX_PROJECT_ROOT={root}",
                         "-e", f"DMUX_RESULTS_DIR={results}", *launcher], allow_server_start=True)
        if not re.fullmatch(r"\$\d+", sid):
            raise SessionError("tmux did not return a numeric session ID")
        self._run(["set-option", "-t", sid, "@dmux-project-root", str(root)])
        self._run(["set-option", "-t", sid, "@dmux-results-dir", str(results)])
        return sid

    def add_window(self, session_id, *, name, project_root, results_dir, command):
        if not re.fullmatch(r"\$\d+", session_id):
            raise SessionError("A numeric session ID is required")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise SessionError("Invalid window name")
        # More than one argv element makes tmux execute directly, without sh -c.
        launcher = self._launcher(command)
        return self._run(["new-window", "-d", "-P", "-F", "#{window_id}", "-t", session_id,
                          "-n", name, "-c", str(project_root),
                          "-e", f"DMUX_PROJECT_ROOT={project_root}",
                          "-e", f"DMUX_RESULTS_DIR={results_dir}", *launcher])

    def prepare_removal(self, selected) -> Removal:
        """Capture every pane, process identity, and server generation for review."""
        sid = selected.get("session_id", "")
        if not re.fullmatch(r"\$\d+", sid):
            raise SessionError("Invalid session target")
        nav = self.navigator
        snapshot = nav.snapshot([])
        if snapshot["error"]:
            raise SessionError(snapshot["error"])
        panes = tuple(pane for pane in snapshot["panes"] if pane["session_id"] == sid)
        if not panes or panes[0]["session"] != selected["session"]:
            raise SessionError("Session disappeared or changed; refresh and select it again")
        if nav.env.get("TMUX"):
            own_socket = nav.env["TMUX"].rsplit(",", 2)[0]
            same_server = not nav.socket or Path(own_socket).resolve() == Path(nav.socket)
            if same_server and any(p["pane_id"] == nav.env.get("TMUX_PANE") for p in panes):
                raise SessionError("Open the browser outside this session before removing it")
        identity = self._run(["display-message", "-p", "-t", panes[0]["target"],
                              "#{pid}\t#{session_created}"])
        if not re.fullmatch(r"\d+\t\d+", identity):
            raise SessionError("Cannot verify the tmux server and session identity")
        fingerprint = (identity, tuple(sorted((p["window_id"], p["pane_id"], p["pane_pid"])
                                              for p in panes)))
        return Removal(sid, panes[0]["session"], fingerprint, panes)

    def remove(self, removal: Removal, *, confirmation: str) -> str:
        if confirmation != removal.name:
            raise SessionError("Type the exact session name to confirm removal")
        latest = self.prepare_removal({"session_id": removal.session_id, "session": removal.name})
        if latest.fingerprint != removal.fingerprint:
            raise SessionError("Session contents changed; review it again before removal")
        self._run(["kill-session", "-t", removal.session_id])
        return f'Removed session {removal.name}. Its windows were closed; result files were kept.'


def main(argv=None):
    """CLI entry point: browser by default, or explicit create/remove."""
    import argparse

    parser = argparse.ArgumentParser(prog="dmux sessions")
    parser.add_argument("action", nargs="?", choices=("browse", "new", "remove"), default="browse")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--tmux-socket", type=Path)
    parser.add_argument("--tmux-client")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--confirm", help="Exact session name; removal terminates its windows and jobs")
    parser.add_argument("--json", action="store_true", help="List existing panes without a UI")
    raw = list(sys.argv[1:] if argv is None else argv)
    command = ()
    if "--" in raw:
        offset = raw.index("--")
        command, raw = raw[offset + 1:], raw[:offset]
    args = parser.parse_args(raw)
    from .settings import Settings
    settings = Settings()
    nav = TmuxNavigator(socket=args.tmux_socket or settings.values['tmux_socket'], client=args.tmux_client)
    manager = SessionManager(nav)
    if command and args.action != "new":
        parser.error("A command after -- is only supported by sessions new")
    try:
        if args.action == "new":
            if not args.name:
                parser.error("sessions new requires a name")
            sid = manager.create(args.name, project_root=args.project_root,
                                 results_dir=args.results_dir, command=command or settings.values['ai_cli'])
            print(f"Created {args.name} ({sid}); open it with dmux sessions")
        elif args.action == "remove":
            panes = nav.snapshot([])["panes"]
            selected = next((p for p in panes if args.name in (p["session"], p["session_id"])), None)
            if selected is None:
                raise SessionError("Session not found; specify an exact name or numeric session ID")
            removal = manager.prepare_removal(selected)
            confirmation = args.confirm
            if confirmation is None:
                if not sys.stdin.isatty():
                    parser.error("Removal requires --confirm EXACT_SESSION_NAME in non-interactive use")
                print(f'Remove {removal.name}: close {len(removal.panes)} panes and terminate their jobs/chats.')
                confirmation = input("Type the session name to confirm: ")
            print(manager.remove(removal, confirmation=confirmation))
        else:
            from .session_browser import browse
            browse(nav, json_output=args.json)
    except SessionError as exc:
        parser.exit(2, f"{exc}\n")
