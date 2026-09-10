"""Command-line interface for dmux."""
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import time

from .monitor import Monitor
from .experiment_view import render_detail, render_stop_confirmation
from .controller import DashboardController
from .bindings import render_help
from .registry import adapter_names, adapter_statuses, load_adapter
from .system import HostSampler
from .refresh import BackgroundRefresh
from .terminal import keyboard
from .tmux import TmuxNavigator, parse_links
from .ui import render_dashboard
from .snapshots import export_snapshot
from . import settings as preferences
from .appearance import Appearance, VersionAction
from .mouse import MouseEvent, MouseMap
from .observations import Observations


def parser(default_adapter: str = "filesystem", *, add_help=True) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="dmux",
        add_help=add_help,
        description=(
            "Terminal monitoring and explicit tmux/process controls for ML experiments. "
            "Closing the dashboard leaves jobs running."
        ),
        epilog=(
            "bare dmux opens all registered experiments. Commands: dmux add PATH · dmux remove NAME · "
            "dmux home · dmux projects list · dmux init · dmux doctor · dmux demo --live · dmux watch · "
            "dmux snapshot · dmux json · dmux report · dmux timeline · dmux settings · dmux completions · "
            "dmux sessions · dmux kill · dmux adapters"
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
    result.add_argument("--interval", type=float, default=None, help="Refresh seconds (default: settings or 2)")
    result.add_argument("--experiment", "--model", dest="model", metavar="TAG", help="Initially focus an experiment tag")
    result.add_argument("--stage", help="Initially inspect a stage instead of following the active one")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Print one expanded dashboard and exit")
    modes.add_argument("--json", action="store_true", help="Print one machine-readable snapshot")
    result.add_argument("--schema-version", type=int, choices=(1, 2), default=2,
                        help="JSON output schema (default: 2; 1 preserves legacy aliases)")
    result.add_argument("--no-gpu", action="store_true", default=None, help="Skip nvidia-smi telemetry")
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
    result.add_argument("--version", action=VersionAction)
    preferences.add_options(result, existing=('interval', 'gpu', 'tmux_socket'))
    result.add_argument('--gpu', dest='no_gpu', action='store_false')
    return result


# Each command keeps its own parser and implementation module.
COMMANDS = {
    "home": "home", "add": "catalog", "remove": "catalog", "projects": "catalog",
    "init": "onboarding", "doctor": "diagnostics", "demo": "demo",
    "sessions": "sessions", "kill": "process_actions",
    "settings": "settings",
    "timeline": "observations",
    "report": "comparison", "completions": "completions",
}


def command_parser(default_adapter="filesystem"):
    result = argparse.ArgumentParser(prog="dmux", description="Terminal experiment workspaces and monitoring")
    result.add_argument("--version", action=VersionAction)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("watch", "snapshot", "json"):
        commands.add_parser(name, parents=[parser(default_adapter, add_help=False)],
                            help={"watch": "Monitor a project", "snapshot": "Print a dashboard",
                                  "json": "Print a snapshot as JSON"}[name])
    for name in COMMANDS:
        commands.add_parser(name, add_help=False, help=f"{name} (use {name} --help for options)")
    commands.add_parser("adapters", help="List available adapters")
    return result


def main(argv=None, *, default_adapter: str = "filesystem", _entry=None, _return_home=False) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        try:
            argv = [preferences.Settings().values['start_view']]
        except (OSError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    elif argv[0].startswith("-") and argv[0] not in {"--help", "-h", "--version"}:
        argv.insert(0, "watch")  # Preserve documented scoped flags without a command.
    argument_parser = command_parser(default_adapter)
    from .completions import complete
    complete(argument_parser)
    args, extra = argument_parser.parse_known_args(argv)
    if args.command in COMMANDS:
        module = importlib.import_module("." + COMMANDS[args.command], __package__)
        forwarded = [args.command, *extra] if args.command in {"add", "remove"} else extra
        try:
            module.main(forwarded)
        except (ValueError, OSError) as exc:
            argument_parser.exit(2, str(exc) + '\n')
        except ImportError as exc:
            if exc.name not in {"psutil", "rich", "jsonschema"} and not (exc.name or "").startswith("rich."):
                raise
            argument_parser.exit(2, f"Missing {exc.name}. Install dmux with its dependencies.\n")
        return
    if extra:
        argument_parser.error("unrecognized arguments: " + " ".join(extra))
    if args.command == "adapters":
        print("\n".join(f"{name}\t{status}" for name, status in adapter_statuses()))
        return
    args.once = args.once or args.command == "snapshot"
    args.json = args.json or args.command == "json"
    try:
        settings = preferences.from_args(args)
    except (OSError, ValueError) as exc:
        argument_parser.error(str(exc))
    args.interval = settings.values['interval']
    args.no_gpu = not settings.values['gpu']
    if args.once and args.json:
        argument_parser.error("snapshot/--once and json/--json are mutually exclusive")
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
    sampler = HostSampler()
    observations = Observations(settings)
    monitor = Monitor(
        queue,
        project_root=args.project_root,
        results_dir=args.results_dir,
        adapter=adapter,
        gpu=lambda: sampler.gpu() if settings.values['gpu'] else {"devices": [], "error": "disabled"},
        processes=lambda: sampler.processes(adapter),
    )
    try:
        links = parse_links(args.tmux_link)
    except ValueError as exc:
        argument_parser.error(str(exc))
    snapshot = monitor.snapshot()
    tmux_config = snapshot.get("tmux_config", {})
    socket = args.tmux_socket or tmux_config.get("socket") or settings.values['tmux_socket']
    if socket and not Path(socket).is_absolute():
        socket = monitor.queue / socket
    navigator = TmuxNavigator(socket=socket, links={**tmux_config.get("links", {}), **links},
                              client=args.tmux_client)

    def fresh_snapshot():
        data = monitor.snapshot()
        navigator.links = {**data.get("tmux_config", {}).get("links", {}), **links}
        data["tmux"] = navigator.snapshot(data["tasks"])
        return observations.observe(data)

    refresh = BackgroundRefresh(fresh_snapshot)
    snapshot["tmux"] = navigator.snapshot(snapshot["tasks"])
    tags = {model["tag"] for model in snapshot["models"]}
    if args.model and tags and args.model not in tags:
        argument_parser.error("Unknown experiment tag; use one from the plan")
    if args.stage and args.stage not in adapter.presentation.stages:
        argument_parser.error("Unknown stage; use one from the plan")
    if tags and set(links) - {"*", *tags}:
        argument_parser.error("Unknown --tmux-link tag; use a plan tag or *")
    if args.json:
        print(json.dumps(export_snapshot(snapshot, version=args.schema_version), indent=2, allow_nan=False))
        return
    if args.once or not console.is_terminal:
        console.print(Appearance(
            render_dashboard(
                snapshot,
                width=console.width,
                height=console.height,
                selected=args.model,
                stage=args.stage,
                expanded=True,
                interactive=False,
                clock=settings.values['clock'], settings=settings,
                presentation=adapter.presentation,
            ), settings))
        return

    controller = DashboardController(snapshot, adapter, navigator, selected=args.model,
                                     stage=args.stage, entry=_entry, return_home=_return_home, settings=settings,
                                     adapter_name=args.adapter)
    controller.snapshot = observations.observe(snapshot)
    try:
        while controller.running:
            navigate, previous_size = None, None
            screen = MouseMap()
            with keyboard(mouse=lambda: settings.values['mouse']) as read_keys, Live(
                console=console, screen=True, auto_refresh=False, vertical_overflow="crop"
            ) as live:
                while controller.running and navigate is None:
                    redraw = False
                    for key in read_keys():
                        redraw = True
                        navigate = (controller.mouse(screen.action(key, console.size))
                                    if isinstance(key, MouseEvent) else controller.key(key))
                        screen.regions = []
                        if navigate is not None or not controller.running:
                            break
                    if controller.picker is not None:
                        redraw = controller.picker.poll() or redraw
                    if controller.comparison is not None:
                        redraw = controller.comparison.poll() or redraw
                        if not controller.comparison.worker.pending:
                            controller.comparison.snapshot = controller.snapshot
                            if controller.comparison.reader.max_points != settings.values['metric_window']:
                                controller.comparison.reader.max_points = settings.values['metric_window']
                                controller.comparison.reader.clear()
                                controller.comparison.worker.request()
                    sampler.gpu_interval = monitor.gpu_interval = settings.values['gpu_interval']
                    if controller.metric_reader.max_points != settings.values['metric_window']:
                        controller.metric_reader.max_points = settings.values['metric_window']
                        controller.metric_reader.clear()
                    if controller.log_view:
                        controller.log_view.limit = settings.values['log_tail']
                    if controller.log_view and controller.log_view.follow:
                        redraw = True
                    if time.monotonic() - controller.updated >= settings.values['interval']:
                        refresh.request()
                        controller.updated = time.monotonic()
                    result = refresh.take()
                    if result is not None:
                        data, error = result
                        if error is not None:
                            controller.notice = f"Refresh unavailable; showing previous snapshot: {error}"
                        else:
                            controller.snapshot = data
                        redraw = True
                    size = console.size
                    if controller.running and navigate is None and (redraw or size != previous_size):
                        c = controller
                        view = (
                            c.options.render(height=size.height) if c.options is not None
                            else c.comparison.render(height=size.height) if c.comparison is not None
                            else c.log_view.render(height=size.height) if c.log_view is not None
                            else render_stop_confirmation(c.pending_stop, c.stop_confirmation, height=size.height)
                            if c.pending_stop is not None
                            else c.picker.render(height=size.height)
                            if c.picker is not None
                            else render_help("detail" if c.detailed else "dashboard", settings)
                            if c.help
                            else render_detail(c.snapshot, c.current_model(), presentation=adapter.presentation,
                                               stage=c.stage, height=size.height, width=size.width, notice=c.notice,
                                               metric_reader=c.metric_reader, metric_offset=c.metric_offset,
                                               return_home=_return_home, preview_reader=c.preview_reader)
                            if c.detailed and c.current_model()
                            else render_dashboard(
                                c.visible_snapshot(), width=size.width, height=size.height,
                                selected=c.current_model(), stage=c.stage, notice=c.notice,
                                clock=settings.values['clock'], settings=settings,
                                presentation=adapter.presentation,
                            )
                        )
                        live.update(screen.frame(Appearance(view, settings)), refresh=True)
                        previous_size = size
                    if controller.running and navigate is None:
                        time.sleep(0.15)
            if controller.running and navigate is not None:
                # Live and keyboard contexts restored the screen and termios first.
                controller.notice = navigator.open(navigate)
                controller.updated = -math.inf
                if _entry == "tmux" and _return_home:
                    controller.running = False
    except KeyboardInterrupt:
        pass
    finally:
        try:
            settings.save()
        except (OSError, ValueError) as exc:
            console.print(f'Settings could not be saved: {exc}')
    console.print("Monitor closed.", style="grey70")


if __name__ == "__main__":
    main()
