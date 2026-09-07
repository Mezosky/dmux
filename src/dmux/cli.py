"""Command-line interface for dmux."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

from .monitor import Monitor
from .experiment_view import render_detail, render_stop_confirmation, selected_task
from .process_actions import ProcessActionError, prepare_stop, stop
from .registry import adapter_names, load_adapter
from .session_browser import SessionBrowser
from .system import gpu_info
from .terminal import keyboard
from .tmux import TmuxNavigator, parse_links
from .ui import render_dashboard


def parser(default_adapter: str = "filesystem") -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="dmux",
        description=(
            "Terminal monitoring and explicit tmux/process controls for ML experiments. "
            "Closing the dashboard leaves jobs running."
        ),
        epilog=(
            "commands: dmux init · dmux doctor · dmux demo --live · dmux watch · "
            "dmux snapshot · dmux json · dmux sessions · dmux kill · dmux adapters"
        ),
    )
    result.add_argument(
        "--adapter",
        default=default_adapter,
        help=f"Filesystem schema adapter (available: {', '.join(adapter_names())})",
    )
    result.add_argument(
        "--project-root",
        type=Path,
        help="Original ML project root used for relative paths (default: current directory)",
    )
    result.add_argument(
        "--results-dir", type=Path,
        help="Read task outputs relative to this directory (relative to project root if not absolute)",
    )
    result.add_argument(
        "--plan-dir", "--queue", dest="queue",
        type=Path, metavar="PLAN_DIR",
        help="Directory containing plan.json (default: project root, then monitor/); --queue is an alias",
    )
    result.add_argument("--interval", type=float, default=2.0, help="Refresh seconds (default: 2)")
    result.add_argument("--experiment", "--model", dest="model", metavar="TAG", help="Initially focus an experiment tag")
    result.add_argument("--stage", help="Initially inspect a stage instead of following the active one")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Print one expanded dashboard and exit")
    modes.add_argument("--json", action="store_true", help="Print one machine-readable snapshot")
    result.add_argument("--no-gpu", action="store_true", help="Skip nvidia-smi telemetry")
    result.add_argument("--tmux-socket", type=Path, help="Optional existing tmux socket path (-S)")
    result.add_argument(
        "--tmux-link",
        action="append",
        default=[],
        metavar="RUN=TARGET",
        help="Link an experiment to an existing tmux target; repeatable (*=TARGET for all)",
    )
    result.add_argument(
        "--tmux-client", help="Explicit client TTY when several clients share a session"
    )
    result.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Override terminal color detection / NO_COLOR",
    )
    result.add_argument("--version", action="version", version="dmux 0.1.0")
    return result


def main(argv=None, *, default_adapter: str = "filesystem") -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv and not argv[0].startswith("-") else "watch"
    if command == "init":
        from .onboarding import main as init_main

        init_main(argv[1:])
        return
    if command == "doctor":
        from .diagnostics import main as doctor_main

        doctor_main(argv[1:])
        return
    if command == "demo":
        from .demo import main as demo_main

        demo_main(argv[1:])
        return
    if command == "sessions":
        from .sessions import main as sessions_main

        sessions_main(argv[1:])
        return
    if command == "kill":
        from .process_actions import main as kill_main

        kill_main(argv[1:])
        return
    if command == "adapters":
        print("\n".join(adapter_names()))
        return
    if command in {"watch", "snapshot", "json"}:
        if argv and argv[0] == command:
            argv = argv[1:]
        if command == "snapshot":
            argv.append("--once")
        elif command == "json":
            argv.append("--json")
    elif argv and not argv[0].startswith("-"):
        parser(default_adapter).error(
            f"Unknown command {command!r}; use init, doctor, watch, snapshot, json, demo, sessions, kill, or adapters"
        )
    argument_parser = parser(default_adapter)
    args = argument_parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < 0.25:
        argument_parser.error("--interval must be finite and >=0.25 seconds")
    try:
        import psutil  # noqa: F401
        from rich.console import Console
        from rich.live import Live
    except ImportError as exc:
        argument_parser.exit(2, f"Missing {exc.name}. Install dmux with its dependencies.\n")

    try:
        adapter = load_adapter(args.adapter)
    except ValueError as exc:
        argument_parser.error(str(exc))
    initial_root = (args.project_root or Path.cwd()).expanduser().resolve()
    queue = args.queue or adapter.default_queue(initial_root)
    console = Console(
        highlight=False,
        color_system=(
            "truecolor" if args.color == "always" else None if args.color == "never" else "auto"
        ),
        no_color=False if args.color == "always" else True if args.color == "never" else None,
    )
    monitor = Monitor(
        queue,
        project_root=args.project_root,
        results_dir=args.results_dir,
        adapter=adapter,
        gpu=(lambda: {"devices": [], "error": "disabled"}) if args.no_gpu else gpu_info,
    )
    try:
        links = parse_links(args.tmux_link)
    except ValueError as exc:
        argument_parser.error(str(exc))
    snapshot = monitor.snapshot()
    tmux_config = snapshot.get("tmux_config", {})
    socket = args.tmux_socket or tmux_config.get("socket")
    if socket and not Path(socket).is_absolute():
        socket = monitor.queue / socket
    navigator = TmuxNavigator(socket=socket, links={**tmux_config.get("links", {}), **links},
                              client=args.tmux_client)

    def fresh_snapshot():
        data = monitor.snapshot()
        navigator.links = {**data.get("tmux_config", {}).get("links", {}), **links}
        data["tmux"] = navigator.snapshot(data["tasks"])
        return data

    snapshot["tmux"] = navigator.snapshot(snapshot["tasks"])
    tags = {model["tag"] for model in snapshot["models"]}
    if args.model and tags and args.model not in tags:
        argument_parser.error("Unknown experiment tag; use one from the plan")
    if args.stage and args.stage not in adapter.presentation.stages:
        argument_parser.error("Unknown stage; use one from the plan")
    if tags and set(links) - {"*", *tags}:
        argument_parser.error("Unknown --tmux-link tag; use a plan tag or *")
    if args.json:
        print(json.dumps(snapshot, indent=2, allow_nan=False))
        return
    if args.once or not console.is_terminal:
        console.print(
            render_dashboard(
                snapshot,
                width=console.width,
                height=console.height,
                selected=args.model,
                stage=args.stage,
                expanded=True,
                presentation=adapter.presentation,
            )
        )
        return

    selected, stage = args.model, args.stage
    updated, running = time.monotonic(), True
    picker, notice = None, None
    detailed, pending_stop, stop_confirmation = False, None, ""
    hidden = set()

    def visible_snapshot():
        visible = [m for m in snapshot["models"] if m["tag"] not in hidden]
        return {**snapshot, "models": visible, "experiments": visible,
                "active": snapshot.get("active") if (snapshot.get("active") or {}).get("model") not in hidden else None}

    def current_model():
        visible = visible_snapshot()
        selected_tag = selected if any(m["tag"] == selected for m in visible["models"]) else None
        return selected_tag or (visible.get("active") or {}).get("model") or next(
            (m["tag"] for m in visible["models"]), None)

    try:
        while running:
            navigate, previous_size = None, None
            with keyboard() as read_keys, Live(
                console=console, screen=True, auto_refresh=False, vertical_overflow="crop"
            ) as live:
                while running and navigate is None:
                    redraw = False
                    for key in read_keys():
                        redraw = True
                        if key == "\x03":
                            running = False
                        elif pending_stop is not None:
                            if key == "\x1b":
                                pending_stop, stop_confirmation = None, ""
                            elif key in "\r\n":
                                try:
                                    notice = stop(pending_stop, confirmation=stop_confirmation)
                                except ProcessActionError as exc:
                                    notice = str(exc)
                                pending_stop, stop_confirmation = None, ""
                                updated = -math.inf
                            elif key in "\b\x7f":
                                stop_confirmation = stop_confirmation[:-1]
                            elif key.isprintable():
                                stop_confirmation += key
                        elif picker is not None:
                            action = picker.key(key)
                            if action:
                                notice = picker.notice
                                picker = None
                                if action[0] == "open":
                                    navigate = action[1]
                                    break
                        elif key in "qQ\x1b":
                            if detailed:
                                detailed = False
                            elif key in "qQ":
                                running = False
                        elif key in "\r\n" and current_model():
                            selected, detailed = current_model(), True
                        elif key in "kK" and current_model():
                            task = selected_task(snapshot, current_model(), stage)
                            try:
                                pending_stop = prepare_stop(snapshot, current_model(),
                                                            task["name"] if key == "k" and task else None)
                                stop_confirmation = ""
                            except ProcessActionError as exc:
                                notice = str(exc)
                        elif key in "xX" and current_model():
                            hidden.add(current_model())
                            selected, stage, detailed = None, None, False
                            notice = "Tab hidden; its processes continue. Press u to restore hidden tabs."
                        elif key in "uU":
                            hidden.clear()
                            notice = None
                        elif key in "tT":
                            notice = None
                            current = current_model()
                            picker = SessionBrowser(navigator, snapshot["tasks"], preferred=current)
                        elif key in "aA":
                            selected, stage = None, None
                        elif key in "nNpP" and visible_snapshot()["models"]:
                            tags_in_order = [model["tag"] for model in visible_snapshot()["models"]]
                            current = current_model()
                            direction = 1 if key in "nN" else -1
                            selected = tags_in_order[
                                (tags_in_order.index(current) + direction) % len(tags_in_order)
                            ]
                            stage = None
                        elif key in "[]":
                            stages = tuple(dict.fromkeys(t["name"] for t in snapshot["tasks"]
                                                         if t["model"] == current_model())) or adapter.presentation.stages
                            task = selected_task(snapshot, current_model(), stage)
                            current_stage = task["name"] if task else stages[0]
                            direction = 1 if key == "]" else -1
                            stage = stages[(stages.index(current_stage) + direction) % len(stages)]
                        elif key in "rR":
                            updated, notice = -math.inf, None
                    if time.monotonic() - updated >= args.interval:
                        snapshot = fresh_snapshot()
                        updated = time.monotonic()
                        redraw = True
                    size = console.size
                    if running and navigate is None and (redraw or size != previous_size):
                        view = (
                            render_stop_confirmation(pending_stop, stop_confirmation, height=size.height)
                            if pending_stop is not None
                            else picker.render(height=size.height)
                            if picker is not None
                            else render_detail(snapshot, current_model(), presentation=adapter.presentation,
                                               stage=stage, height=size.height, notice=notice)
                            if detailed and current_model()
                            else render_dashboard(
                                visible_snapshot(),
                                width=size.width,
                                height=size.height,
                                selected=current_model(),
                                stage=stage,
                                notice=notice,
                                presentation=adapter.presentation,
                            )
                        )
                        live.update(view, refresh=True)
                        previous_size = size
                    if running and navigate is None:
                        time.sleep(0.15)
            if running and navigate is not None:
                # Live and keyboard contexts restored the screen and termios first.
                notice = navigator.open(navigate)
                updated = -math.inf
    except KeyboardInterrupt:
        pass
    console.print("Monitor closed.", style="grey70")


if __name__ == "__main__":
    main()
