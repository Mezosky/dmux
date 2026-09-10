"""Adapter-neutral aggregation of experiment and machine state."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
import shutil
import time
from typing import Callable, Mapping

from .adapters.base import resolve_path
from .adapters.filesystem import FilesystemAdapter
from .connectors import JsonCache, tail_log
from .projects import experiment_roster, project_paths, run_tag
from .system import gpu_info, retain_gpu_reading, running_processes
from .status import scheduler_records
from .templates import expand_templates


class Monitor:
    """Create read-only monitoring snapshots from an experiment filesystem.

    Relative task paths are resolved against ``project_root`` (or a root declared
    by the plan), never against dmux's installation directory.
    """

    def __init__(
        self,
        queue: str | Path,
        *,
        project_root: str | Path | None = None,
        results_dir: str | Path | None = None,
        adapter=None,
        processes: Callable[[], list[dict]] | None = None,
        gpu: Callable[[], dict] = gpu_info,
    ) -> None:
        self.adapter = adapter or FilesystemAdapter()
        initial_root = Path(project_root).expanduser().resolve() if project_root else Path.cwd()
        self.queue = resolve_path(queue, initial_root)
        self.project_root = initial_root
        self.explicit_project_root = project_root is not None
        self.results_dir = results_dir
        self.json = JsonCache()
        self.trackers: dict = {}
        self.process_source = processes or (lambda: running_processes(self.adapter))
        self.gpu_source = gpu
        self.gpu_cache: dict = {"devices": [], "error": None}
        self.gpu_time = -math.inf

    def _plan_root(self, plan: Mapping) -> Path:
        if self.explicit_project_root:
            return self.project_root
        declared = plan.get("project_root")
        if declared:
            return resolve_path(declared, self.queue)
        return self.project_root

    @staticmethod
    def _normalized_task(task: Mapping) -> dict:
        normalized = dict(task)
        normalized["model"] = run_tag(task)
        normalized["name"] = task.get("name", task.get("stage", "run"))
        return normalized

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        plan = self.json.read(self.queue / "plan.json")
        if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
            return {
                "schema_version": 1,
                "state": "waiting",
                "queue": str(self.queue),
                "plan_dir": str(self.queue),
                "project_root": str(self.project_root),
                "updated": now,
                "adapter": self.adapter.presentation.name,
                "presentation": asdict(self.adapter.presentation),
                "experiments": [],
                "message": "Waiting for a readable plan.json",
                "models": [],
                "tasks": [],
                "warnings": list(self.json.warnings.values()),
            }

        try:
            project_root = self._plan_root(plan)
            plan = expand_templates(plan, project_root, self.results_dir)
            self.adapter.configure(plan)
            locations = project_paths(plan, project_root, self.results_dir)
        except (ValueError, OSError) as exc:
            return {
                "schema_version": 1,
                "state": "invalid",
                "queue": str(self.queue),
                "plan_dir": str(self.queue),
                "project_root": str(self.project_root),
                "updated": now,
                "adapter": self.adapter.presentation.name,
                "presentation": asdict(self.adapter.presentation),
                "experiments": [],
                "message": f"Invalid plan.json: {exc}",
                "models": [],
                "tasks": [],
                "warnings": [str(exc), *self.json.warnings.values()],
            }
        advisory_key, exit_codes, status_warnings = scheduler_records(
            self.json.read(self.queue / "status.json", {}),
            self.json.read(self.queue / "completion.json", []),
        )

        processes = self.process_source()
        queue_jobs = [
            process
            for process in processes
            if process["script"] == self.adapter.queue_script
            and process["out"] == str(self.queue)
        ]
        by_destination: dict[str | None, list[dict]] = {}
        for process in processes:
            if process["script"] != self.adapter.queue_script:
                by_destination.setdefault(process["out"], []).append(process)

        tasks: list[dict] = []
        warnings: list[str] = status_warnings
        prepare = getattr(self.adapter, "prepare_poll", None)
        if prepare is not None:
            prepare([(self._normalized_task(task), self.adapter.task_directory(task, locations[task.get("project")].results))
                     for task in plan["tasks"]], now=now)
        if not self.explicit_project_root and not plan.get("project_root") and any(
            not task.get("project") and not self.results_dir and not plan.get("results_dir")
            and task.get("directory") and not Path(task["directory"]).is_absolute()
            for task in plan["tasks"]
        ):
            warnings.append(
                "Relative task paths use the current directory; pass --project-root "
                "or declare project_root in plan.json"
            )
        for raw_task in plan["tasks"]:
            task = self._normalized_task(raw_task)
            location = locations[raw_task.get("project")]
            path = self.adapter.task_directory(task, location.results)
            jobs = sorted(by_destination.get(str(path), []), key=lambda p: (p["started"], p["pid"]))
            if task.get("process", {}).get("script"):
                jobs = [p for p in jobs if p["script"] == task["process"]["script"]]
            job = next(iter(jobs), None)
            key = (task["model"], task["name"])
            is_advisory = key == advisory_key
            progress, marker, detail, task_warnings = self.adapter.inspect_task(
                task, path, self.json, self.trackers, now
            )
            warnings.extend(f'{task["model"]}/{task["name"]}: {text}' for text in task_warnings)
            state = self.adapter.state(
                task,
                progress,
                alive=bool(job),
                exit_code=exit_codes.get(key),
                marker=marker,
                stale_active=is_advisory and not queue_jobs,
            )
            if is_advisory and queue_jobs and not job and state == "queued":
                state = "starting"
            if state == "invalid":
                warnings.append(
                    f'{task["model"]}/{task["name"]}: output integrity/count issue — inspect files'
                )
            expected = self.adapter.expected(task, progress)
            log = self.adapter.task_log(task, self.queue, path)
            tasks.append(
                {
                    "model": task["model"],
                    "experiment": task["model"],
                    "project": raw_task.get("project"),
                    "project_root": str(location.root),
                    "results_dir": str(location.results),
                    "name": task["name"],
                    "stage": task["name"],
                    "label": task.get("label", self.adapter.presentation.stage_label(task["name"])),
                    "state": state,
                    "expected": expected,
                    "counted": bool(raw_task.get("progress")) or progress is not None or expected is not None,
                    "progress": progress,
                    "detail": detail,
                    "pid": job["pid"] if job else None,
                    "process_started": job["started"] if job else None,
                    "process_command": job.get("command") if job else None,
                    "processes": [{"pid": p["pid"], "started": p["started"], "command": p.get("command")}
                                  for p in jobs],
                    "elapsed_seconds": now - job["started"] if job else None,
                    "directory": str(path) if path else None,
                    "metadata": {**plan.get("metadata", {}),
                                 **plan.get("projects", {}).get(raw_task.get("project"), {}).get("metadata", {}),
                                 **task.get("metadata", {})},
                    "outputs": list(task.get("outputs", [])),
                    "metrics": list(task.get("metrics", [])),
                    "scheduler": task.get("scheduler"),
                    "log": str(log) if log is not None else None,
                }
            )

        active = next((task for task in tasks if task["pid"]), None)
        active = active or next(
            (task for task in tasks if task["state"] in {"starting", "interrupted"}), None
        )
        errors = any(task["state"] in {"failed", "invalid"} for task in tasks)
        pause = self.adapter.pause_requested(self.queue)
        if errors:
            state = "running" if active and active["pid"] else "failed"
        elif active and active["pid"]:
            state = "pause requested" if pause else "running"
        elif tasks and all(task["state"] == "complete" for task in tasks):
            state = "complete"
        elif queue_jobs:
            state = "pause requested" if pause else "starting"
        elif any(task["state"] == "scheduler running" for task in tasks):
            state = "scheduler running"
        elif any(task["state"] == "scheduled" for task in tasks):
            state = "scheduled"
        elif any(task["state"] == "unavailable" for task in tasks):
            state = "unavailable"
        elif pause:
            state = "paused"
        else:
            state = (
                "interrupted"
                if any(task["state"] in {"partial", "interrupted"} for task in tasks)
                else "idle"
            )
        if self.adapter.queue_script and not queue_jobs and active and active["pid"]:
            warnings.append("Experiment is running but its configured scheduler was not found")

        order = list(dict.fromkeys(task["model"] for task in tasks))
        roster = {run_tag({**row, "run": row["tag"]}): row for row in experiment_roster(plan)}
        order += [tag for tag in roster if tag not in order]
        models = []
        for tag in order:
            model_tasks = [task for task in tasks if task["model"] == tag]
            counted = [task for task in model_tasks if task["counted"]]
            expected = (sum(task["expected"] for task in counted)
                        if counted and all(task["expected"] is not None for task in counted) else None)
            saved = sum((task["progress"] or {}).get("saved", 0) for task in model_tasks)
            models.append(
                {
                    "tag": tag,
                    "label": self.adapter.presentation.entity_name(tag),
                    "model_id": roster.get(tag, {}).get("model_id", tag),
                    "blocked": not bool(model_tasks),
                    "reason": roster.get(tag, {}).get("reason") or ("No stages declared" if not model_tasks else None),
                    "expected": expected,
                    "counted": bool(counted),
                    "saved": saved,
                    "completed_stages": sum(
                        task["state"] == "complete" for task in model_tasks
                    ),
                    "stages": len(model_tasks),
                }
            )

        if now - self.gpu_time >= 5:
            self.gpu_cache = retain_gpu_reading(self.gpu_cache, self.gpu_source())
            self.gpu_time = now
        disk = shutil.disk_usage(self.queue)
        if disk.free < self.adapter.disk_warning_gib * 1024**3:
            warnings.append(
                f"Disk below the configured {self.adapter.disk_warning_gib:g} GiB warning floor"
            )
        links = dict(plan.get("tmux", {}).get("links", {}))
        for raw_task in plan["tasks"]:
            location = locations[raw_task.get("project")]
            session = raw_task.get("tmux_session") or location.tmux_session
            if session:
                links.setdefault(run_tag(raw_task), session)
        return {
            "schema_version": 1,
            "state": state,
            "adapter": self.adapter.presentation.name,
            "presentation": asdict(self.adapter.presentation),
            "queue": str(self.queue),
            "plan_dir": str(self.queue),
            "project_root": str(project_root),
            "results_dir": str(locations[None].results),
            "projects": {name: {"root": str(value.root), "results_dir": str(value.results)}
                         for name, value in locations.items() if name is not None},
            "tmux_config": {**plan.get("tmux", {}), "links": links},
            "updated": now,
            "tasks": tasks,
            "models": models,
            "experiments": models,
            "active": active,
            "queue_pid": queue_jobs[0]["pid"] if queue_jobs else None,
            "expected": (sum(model["expected"] for model in models if model["counted"])
                         if any(model["counted"] for model in models)
                         and all(model["expected"] is not None for model in models if model["counted"])
                         else None),
            "saved": sum(model["saved"] for model in models),
            "completed_stages": sum(task["state"] == "complete" for task in tasks),
            "total_stages": len(tasks),
            "valid": sum((task["progress"] or {}).get("valid", 0) for task in tasks),
            "excluded": sum(
                (task["progress"] or {}).get("excluded", 0) for task in tasks
            ),
            "gpu": self.gpu_cache,
            "disk_free_gib": disk.free / 1024**3,
            "warnings": warnings + list(self.json.warnings.values()),
            "recent": (
                tail_log(active["log"], formatter=self.adapter.format_log) if active else []
            ),
        }
