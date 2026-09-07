"""User-confirmed SIGTERM requests, separate from read-only monitoring."""
from __future__ import annotations

from dataclasses import dataclass

import psutil


class ProcessActionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessTarget:
    pid: int
    started: float
    command: tuple[str, ...]


@dataclass(frozen=True)
class StopRequest:
    label: str
    roots: tuple[ProcessTarget, ...]
    targets: tuple[ProcessTarget, ...]


def _identity(process):
    return ProcessTarget(process.pid, process.create_time(), tuple(process.cmdline()))


def _process(target):
    process = psutil.Process(target.pid)
    if _identity(process) != target:
        raise ProcessActionError(f"PID {target.pid} changed; refresh before stopping it")
    return process


def _tree(roots):
    protected = {psutil.Process().pid, *(process.pid for process in psutil.Process().parents())}
    found = {}
    for root in roots:
        process = _process(root)
        for child in [process, *process.children(recursive=True)]:
            if child.pid in protected:
                raise ProcessActionError("Cannot stop dmux itself or its parent processes")
            target = _identity(child)
            found[target.pid] = target
    return tuple(sorted(found.values(), key=lambda target: target.pid))


def prepare_stop(snapshot, model: str, stage: str | None = None) -> StopRequest:
    tasks = [task for task in snapshot["tasks"] if task["model"] == model
             and (stage is None or task["name"] == stage) and task.get("pid")]
    if not tasks:
        raise ProcessActionError("No live experiment process matches this selection")
    roots = []
    for task in tasks:
        identities = task.get("processes") or [{"pid": task["pid"], "started": task.get("process_started"),
                                               "command": task.get("process_command")}]
        for identity in identities:
            if identity.get("started") is None or not identity.get("command"):
                raise ProcessActionError("Process identity is unavailable; refresh before stopping it")
            roots.append(ProcessTarget(identity["pid"], identity["started"], tuple(identity["command"])))
    roots = list(dict.fromkeys(roots))
    try:
        targets = _tree(roots)
    except (psutil.Error, OSError) as exc:
        raise ProcessActionError(f"Cannot verify selected processes: {exc}") from exc
    return StopRequest(f"{model}/{stage}" if stage else model, tuple(roots), targets)


def stop(request: StopRequest, *, confirmation: str) -> str:
    if confirmation != request.label:
        raise ProcessActionError("Confirmation did not match; no signals sent")
    try:
        if _tree(request.roots) != request.targets:
            raise ProcessActionError("Process tree changed; review the selection again")
        # Validate the whole selection before sending anything; psutil additionally
        # checks PID reuse in terminate(). Never escalate to SIGKILL automatically.
        processes = [_process(target) for target in request.targets]
    except (psutil.Error, OSError) as exc:
        raise ProcessActionError(f"Process state changed; no signals sent: {exc}") from exc
    sent, errors = [], []
    for process, target in zip(processes, request.targets):
        try:
            _process(target).terminate()
            sent.append(process.pid)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, ProcessActionError) as exc:
            errors.append(f"{process.pid}: {exc}")
    message = f"SIGTERM requested for PIDs {', '.join(map(str, sent)) or 'none'}."
    if errors:
        message += " Not signaled: " + "; ".join(errors)
    return message


def main(argv=None):
    import argparse
    import sys
    from pathlib import Path
    from .monitor import Monitor
    from .registry import load_adapter

    parser = argparse.ArgumentParser(prog="dmux kill", description="Request SIGTERM for selected experiment processes")
    parser.add_argument("--experiment", "--model", dest="model", required=True)
    parser.add_argument("--stage", help="Omit to stop every currently live stage of the experiment")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--plan-dir", "--queue", dest="queue", type=Path)
    parser.add_argument("--adapter", default="filesystem")
    parser.add_argument("--confirm", help="Exact experiment[/stage] label")
    args = parser.parse_args(argv)
    try:
        adapter = load_adapter(args.adapter)
        root = (args.project_root or Path.cwd()).resolve()
        snapshot = Monitor(args.queue or adapter.default_queue(root), project_root=args.project_root,
                           results_dir=args.results_dir, adapter=adapter,
                           gpu=lambda: {"devices": [], "error": "disabled"}).snapshot()
        request = prepare_stop(snapshot, args.model, args.stage)
        print(f"Stop {request.label}: " + ", ".join(str(p.pid) for p in request.targets))
        print("Chats and results are kept. If a scheduler is used, it may schedule more work.")
        confirmation = args.confirm
        if confirmation is None:
            if not sys.stdin.isatty():
                parser.error("Non-interactive use requires --confirm EXACT_EXPERIMENT[/STAGE]")
            confirmation = input(f"Type {request.label} to send SIGTERM: ")
        print(stop(request, confirmation=confirmation))
    except (ValueError, ProcessActionError) as exc:
        parser.exit(2, f"{exc}\n")
