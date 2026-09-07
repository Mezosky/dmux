"""Explicit setup wizard: write one new plan, never modify experiment outputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys

from .adapters.base import resolve_path
from .adapters.filesystem import FilesystemAdapter
from .discovery import discover_outputs, integer_fields, sample_file, suggest


def _ask(label, default="", *, interactive):
    if not interactive:
        return default
    suffix = f" [{default}]" if default else " (optional; Enter skips)"
    answer = input(label + suffix + ": ").strip()
    return answer or default


def create_plan(path: Path, plan: dict) -> None:
    """Exclusive creation also refuses symlinks and races with another writer."""
    FilesystemAdapter().configure(plan)
    payload = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dmux init", description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--plan-dir", type=Path, default=Path("monitor"))
    parser.add_argument("--experiment", default="my-experiment")
    parser.add_argument("--stage", default="train")
    parser.add_argument("--format", choices=("json", "jsonl", "files", "completion"))
    parser.add_argument("--source", help="File path or file-count glob, relative to results")
    parser.add_argument("--current-field")
    parser.add_argument("--total-field")
    parser.add_argument("--identity", help="Comma-separated JSONL identity fields")
    parser.add_argument("--expected", type=int, help="Known total; omit when unknown")
    parser.add_argument("--log", help="Optional log path, relative to results")
    parser.add_argument("--script", help="Optional worker script basename for process matching")
    parser.add_argument("--output-flag", default="--out", help="Use --output-flag=--output-dir for a custom flag")
    parser.add_argument("--yes", action="store_true", help="Use supplied flags/defaults without prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print plan JSON without creating anything")
    args = parser.parse_args(argv)
    interactive = not args.yes and not args.dry_run
    if interactive and not sys.stdin.isatty():
        parser.error("Run init in a terminal, or use --yes with your output settings (add --dry-run to preview)")
    root = args.project_root.expanduser().resolve()
    if not root.is_dir():
        parser.error("Project root must be an existing directory")
    plan_path = resolve_path(args.plan_dir, root) / "plan.json"
    if plan_path.exists() or plan_path.is_symlink():
        parser.error(f"Refusing to overwrite {plan_path}; choose a different --plan-dir")
    if args.expected is not None and args.expected < 0:
        parser.error("--expected must be non-negative")
    try:
        if interactive:
            print(f"DMUX / CONNECT A PROJECT\nProject: {root}\nOnly a new plan.json will be written.")
        results = resolve_path(
            str(args.results_dir) if args.results_dir else
            _ask("Results directory", "outputs", interactive=interactive), root)
        candidates, limited = discover_outputs(results)
        if interactive:
            print("Detected files: " + (", ".join(str(p) for p in candidates[:12]) or "none yet"))
            if limited:
                print("Discovery limit reached; you can enter an exact path.")
        preferred = next((p for p in candidates if p.suffix in {".json", ".jsonl"}), None)
        guess_path = resolve_path(args.source, results) if args.source else results / preferred if preferred else None
        guessed, _ = sample_file(guess_path) if guess_path else ("json", None)
        kind = args.format or _ask("Format: json / jsonl / files / completion", guessed, interactive=interactive)
        if kind not in {"json", "jsonl", "files", "completion"}:
            parser.error("Choose json, jsonl, files, or completion")
        defaults = {"json": "progress.json", "jsonl": "metrics.jsonl", "files": "*.ckpt", "completion": "DONE"}
        source_default = str(preferred) if preferred and kind == guessed else defaults[kind]
        source = args.source or _ask("Progress file or glob", source_default, interactive=interactive)
        sample = sample_file(resolve_path(source, results))[1] if kind in {"json", "jsonl"} else None
        fields = integer_fields(sample)
        if interactive and fields:
            print("Integer fields found: " + ", ".join(fields))
        tag = _ask("Experiment name", args.experiment, interactive=interactive)
        stage = _ask("Stage name", args.stage, interactive=interactive)
        task = {"experiment": tag, "stage": stage, "directory": ".", "outputs": [source]}
        if kind == "json":
            current = args.current_field or _ask("Current counter field", suggest(fields,
                ("current", "step", "global_step", "training.step", "training.epoch", "epoch"), "current"),
                interactive=interactive)
            total = args.total_field
            if total is None:
                total = _ask("Total counter field", suggest(fields,
                    ("total", "max_steps", "total_steps", "training.total", "training.total_epochs", "total_epochs"),
                    "total" if sample is None else ""), interactive=interactive)
            task["progress"] = {"type": "json", "path": source, "current_field": current}
            if total:
                task["progress"]["total_field"] = total
        elif kind == "jsonl":
            scalar = [k for k, v in (sample or {}).items() if isinstance(v, (str, int)) and not isinstance(v, bool)]
            identity = args.identity or _ask("Unique record field(s), comma separated",
                suggest(scalar, ("id", "sample_id", "record_id", "iteration", "step", "epoch"), "id"),
                interactive=interactive)
            task["progress"] = {"type": "jsonl", "path": source,
                                "identity": [field.strip() for field in identity.split(",") if field.strip()]}
            if interactive:
                print("JSONL counts unique committed records, not skipped training iterations.")
        elif kind == "files":
            task["progress"] = {"type": "files", "glob": source}
        else:
            task["completion"] = {"type": "file", "path": source}
        expected = args.expected
        if expected is None and kind in {"jsonl", "files"}:
            entered = _ask("Expected record/file count (leave blank if unknown)", interactive=interactive)
            expected = int(entered) if entered else None
        if expected is not None:
            if kind == "completion":
                parser.error("--expected is for numeric connectors, not a completion artifact")
            task["expected"] = expected
        log = args.log if args.log is not None else _ask("Log path", interactive=interactive)
        if log:
            task["log"] = log
            if log not in task["outputs"]:
                task["outputs"].append(log)
        script = args.script if args.script is not None else _ask("Worker script basename (optional PID matching)", interactive=interactive)
        if script:
            flag = args.output_flag
            if interactive:
                flag = _ask("Worker output-directory flag", flag, interactive=True)
            task["process"] = {"script": script, "output_flag": flag}
        plan = {"name": root.name, "project_root": str(root), "results_dir": str(results),
                "tasks": [task]}
        FilesystemAdapter().configure(plan)
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return
        if interactive:
            print(f"\nPlan: {plan_path}\n" + json.dumps(plan, indent=2))
            if _ask("Create this plan? yes/no", "yes", interactive=True).lower() not in {"yes", "y"}:
                print("Cancelled; nothing written.")
                return
        create_plan(plan_path, plan)
        print(f"Created {plan_path}. Experiment files are unchanged.")
        flags = f"--project-root {shlex.quote(str(root))} --plan-dir {shlex.quote(str(plan_path.parent))}"
        print(f"Check: dmux doctor {flags}\nOpen:  dmux watch {flags}")
        if not script:
            print("PID matching is not configured; add a task.process block to monitor worker liveness.")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Setup failed: {exc}\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(130, "\nCancelled; no plan was written.\n")
