"""Read-only connection checks with actionable, machine-readable results."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from .adapters.base import resolve_path
from .adapters.filesystem import FilesystemAdapter
from .connectors import JsonCache
from .discovery import integer_fields, sample_file
from .monitor import Monitor
from .projects import project_paths


def diagnose(plan_dir, *, project_root=None, results_dir=None, processes=None) -> dict:
    """Observe configuration, outputs and real processes; never run project code.

    One bounded snapshot is inspected. A growing JSONL file may need subsequent
    dashboard polls to catch up; a successful doctor is not a full-history audit.
    """
    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
    directory = resolve_path(plan_dir, root)
    checks = []

    def add(level, code, message, *, scope="project", path=None, hint=None):
        checks.append({"level": level, "code": code, "scope": scope,
                       "message": message, "path": str(path) if path is not None else None,
                       "hint": hint})

    def report():
        counts = dict(Counter(item["level"] for item in checks))
        code = 2 if counts.get("error") else 1 if counts.get("warning") else 0
        return {"plan_dir": str(directory), "checks": checks, "counts": counts, "exit_code": code}

    path = directory / "plan.json"
    if not path.is_file():
        add("error", "plan_missing", "No readable plan.json at this location.", path=path,
            hint="Run dmux init or pass --plan-dir to the directory containing your plan.")
        return report()
    cache = JsonCache()
    plan = cache.read(path)
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list) or cache.warnings:
        add("error", "plan_unreadable", "Plan must be valid JSON with a tasks list (maximum 16 MiB).",
            path=path, hint="Check JSON syntax and use dmux init --dry-run for a starter plan.")
        return report()
    try:
        adapter = FilesystemAdapter()
        adapter.configure(plan)
        effective_root = root if project_root else resolve_path(plan.get("project_root", root), directory)
        locations = project_paths(plan, effective_root, results_dir)
        # Refuse special files before any connector can block on a pipe/device.
        sources = {directory / "status.json", directory / "completion.json"}
        for task in plan["tasks"]:
            base = adapter.task_directory(task, locations[task.get("project")].results)
            if base:
                for connector in (task.get("progress") or {}, task.get("completion") or {}):
                    if connector.get("path"):
                        sources.add(resolve_path(connector["path"], base))
                if task.get("log"):
                    sources.add(resolve_path(task["log"], base))
        for source in sorted(sources):
            if source.exists() and not source.is_file():
                add("error", "not_a_file", "Configured input is not a regular file.", path=source,
                    hint="Point file connectors at regular output files, not directories or pipes.")
        if any(item["level"] == "error" for item in checks):
            return report()
        monitor = Monitor(directory, project_root=project_root, results_dir=results_dir,
                          adapter=adapter, processes=processes,
                          gpu=lambda: {"devices": [], "error": "not probed by doctor"})
        snapshot = monitor.snapshot()
        if snapshot["state"] in {"waiting", "invalid"}:
            raise ValueError(snapshot["message"])
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        add("error", "configuration", str(exc), path=path,
            hint="Check field names, connector types and resolved paths in plan.json.")
        return report()

    add("ok", "plan", f'Loaded {len(snapshot["tasks"])} stage(s).', path=path)
    if not snapshot["tasks"]:
        add("warning", "no_tasks", "No stages are configured.", hint="Add an experiment task to the plan.")
    for name, location in locations.items():
        scope = name or "project"
        for label, source in (("project root", location.root), ("results directory", location.results)):
            add("ok" if source.is_dir() else "warning", "directory",
                f'{label}: {"found" if source.is_dir() else "not present yet"}.', scope=scope, path=source,
                hint=None if source.is_dir() else "Check this path; dmux will not create experiment directories.")
    for raw, task in zip(plan["tasks"], snapshot["tasks"]):
        scope = f'{task["experiment"]}/{task["stage"]}'
        base = Path(task["directory"]) if task["directory"] else None
        config = raw.get("progress", {})
        progress = task.get("progress") or {}
        if base and not base.is_dir():
            add("warning", "task_directory", "Stage output directory is not present yet.", scope=scope, path=base,
                hint="Verify directory relative to results_dir; future outputs can remain pending.")
        if config and base:
            source = resolve_path(config["path"], base) if config.get("path") else base
            if config["type"] != "files" and not source.is_file():
                add("warning", "source_missing", "Progress file is not present yet.", scope=scope, path=source,
                    hint="Check progress.path relative to the stage directory.")
            elif config["type"] == "json":
                guessed, sample = sample_file(source)
                if guessed == "jsonl":
                    add("error", "format_mismatch", "This file contains newline-delimited JSON, not one JSON document.",
                        scope=scope, path=source, hint="Use type=jsonl and an identity field for unique committed records.")
                elif progress.get("malformed"):
                    fields = ", ".join(integer_fields(sample)) or "none in bounded sample"
                    add("error", "counter_fields", "Current/total fields must contain non-negative integer counters.",
                        scope=scope, path=source, hint=f"Integer fields found: {fields}. Set current_field/total_field; omit an unknown total.")
                elif progress:
                    add("ok", "counter", f'{progress["saved"]:,} saved; total {task["expected"] if task["expected"] is not None else "unknown"}.',
                        scope=scope, path=source)
            elif config["type"] == "files":
                count = progress.get("saved", 0)
                empty = count == 0 and task["expected"] != 0
                add("warning" if empty else "ok", "file_count", f'{count:,} files match {config.get("glob", "*")!r}.',
                    scope=scope, path=source,
                    hint="Check the glob; future checkpoints may legitimately be absent." if empty else None)
            if task["expected"] is None:
                add("info", "unknown_total", "Total is unknown; only saved counts will be shown.", scope=scope,
                    hint="Set expected or a total_field only when your experiment provides a real total.")
            for key in ("duplicates", "malformed", "unexpected"):
                if progress.get(key):
                    hint = ("Verify identity fields identify one record each; duplicate/unexpected rows never add progress."
                            if config["type"] == "jsonl" else "Check numeric fields, expected totals and file patterns.")
                    add("error", key, f'{progress[key]:,} {key} record(s)/count issue(s).', scope=scope, path=source, hint=hint)
            if progress.get("partial_write"):
                add("warning", "partial_write", "Uncommitted final JSONL row is excluded.", scope=scope, path=source,
                    hint="Wait for the writer to commit a newline, then check again.")
            if config["type"] == "jsonl":
                tracker = next((t for t in monitor.trackers.values() if t.path == source), None)
                try:
                    unread = tracker and tracker.reader.offset < source.stat().st_size
                except OSError:
                    unread = False  # Missing/future sources are reported above.
                if unread:
                    add("warning", "read_budget", "Only the first bounded batch of this JSONL file was checked.", scope=scope,
                        path=source, hint="Open dmux watch; subsequent polls incrementally read the remaining records.")
                add("info", "record_count", f'{progress.get("saved", 0):,} committed unique records checked.', scope=scope, path=source)
        if raw.get("completion"):
            completion = raw["completion"]
            if base:
                for relative in [completion["path"], *completion.get("required", [])]:
                    source = resolve_path(relative, base)
                    if not source.is_file():
                        add("warning", "completion_missing", "Completion artifact is not present yet.", scope=scope,
                            path=source, hint="Check completion.path/required; a running or future stage may legitimately be pending.")
            add("ok" if task["state"] == "complete" else "info", "completion",
                f'Completion stage state: {task["state"]}.', scope=scope,
                hint="Completion artifacts establish state, not elapsed-time percentages.")
        if task["log"]:
            found = Path(task["log"]).is_file()
            add("ok" if found else "warning", "log", "Log found." if found else "Configured log is not present yet.",
                scope=scope, path=task["log"], hint=None if found else "Check log relative to the stage directory.")
        else:
            add("info", "no_log", "No log configured (optional).", scope=scope)
        process = raw.get("process", {})
        if task["pid"]:
            add("ok", "process", "Matched actual PID(s): " + ", ".join(str(p["pid"]) for p in task["processes"]), scope=scope)
        elif process.get("script"):
            add("info" if task["state"] == "complete" else "warning", "process_unmatched",
                "No matching live process was found; status JSON is not proof of liveness.", scope=scope,
                hint=f'Match script basename {process["script"]!r} and {process.get("output_flag", "--out")} resolving to {base}; completed workers need no PID.')
        else:
            add("info", "no_process", "PID matching is not configured (optional).", scope=scope,
                hint="Add process.script and process.output_flag; dmux never starts the project for you.")
    for warning in snapshot["warnings"]:
        add("error" if warning in monitor.json.warnings.values() else "warning", "snapshot", warning)
    for tool in ("tmux", "nvidia-smi"):
        add("info", "optional_tool", f'{tool}: {"available" if shutil.which(tool) else "not installed (optional)"}.')
    return report()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dmux doctor", description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--plan-dir", "--queue", dest="plan_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Print diagnostic JSON")
    args = parser.parse_args(argv)
    root = (args.project_root or Path.cwd()).expanduser().resolve()
    report = diagnose(args.plan_dir or FilesystemAdapter().default_queue(root),
                      project_root=args.project_root, results_dir=args.results_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console(highlight=False)
        console.print(Panel(Text("DMUX / CONNECTION CHECK", style="bold bright_cyan"), border_style="cyan"))
        styles = {"ok": "green", "info": "cyan", "warning": "yellow", "error": "red"}
        for check in report["checks"]:
            line = Text(f'{check["level"].upper():7} ', style=styles[check["level"]])
            line.append(f'{check["scope"]}: {check["message"]}', style="default")
            if check["path"]:
                line.append(f'\n        {check["path"]}', style="dim")
            if check["hint"]:
                line.append(f'\n        {check["hint"]}', style="dim")
            console.print(line)
        counts = report["counts"]
        console.print(Text(f'{counts.get("error", 0)} errors · {counts.get("warning", 0)} warnings · read-only check'))
    if report["exit_code"]:
        raise SystemExit(report["exit_code"])
