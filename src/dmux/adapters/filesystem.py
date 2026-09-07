"""Declarative adapter for common JSON, JSONL, log, and artifact layouts."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import islice
import math
from pathlib import Path
from typing import Mapping, Sequence

from ..connectors import IncrementalJsonlReader, JsonCache
from .base import Presentation, command_option, resolve_path
from ..projects import run_tag


def _field(value, dotted: str, default=None):
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value


def _tuple(record: Mapping, fields: Sequence[str]) -> tuple:
    missing = object()
    values = tuple(_field(record, field, missing) for field in fields)
    if any(value is missing for value in values):
        raise KeyError("missing identity field")
    return values


@dataclass
class RecordTracker:
    """Schema-configurable unique-record tracker for arbitrary JSONL metrics."""

    path: Path
    config: Mapping
    reader: IncrementalJsonlReader = field(init=False)
    seen: set[tuple] = field(default_factory=set)
    semantic: set[tuple] = field(default_factory=set)
    statuses: Counter = field(default_factory=Counter)
    groups: Counter = field(default_factory=Counter)
    schema_malformed: int = 0
    duplicates: int = 0
    unexpected: int = 0
    last_group: object = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.reader = IncrementalJsonlReader(self.path)

    @property
    def count(self) -> int:
        return len(self.seen)

    def _reset_counts(self) -> None:
        self.seen.clear()
        self.semantic.clear()
        self.statuses.clear()
        self.groups.clear()
        self.schema_malformed = self.duplicates = self.unexpected = 0
        self.last_group = None

    def update(self) -> None:
        records, reset = self.reader.read()
        if reset:
            self._reset_counts()
        identity_fields = tuple(self.config.get("identity", ("id",)))
        semantic_fields = tuple(self.config.get("semantic_identity", ()))
        group_field = self.config.get("group_field")
        status_field = self.config.get("status_field")
        allowed = self.config.get("allowed_values", {})
        for record in records:
            try:
                identity = _tuple(record, identity_fields)
                semantic = _tuple(record, semantic_fields) if semantic_fields else None
                status = _field(record, status_field) if status_field else "ok"
                group = _field(record, group_field) if group_field else None
                if status_field and status is None:
                    raise KeyError("missing status")
            except (KeyError, TypeError):
                self.schema_malformed += 1
                continue
            if identity in self.seen or (semantic is not None and semantic in self.semantic):
                self.duplicates += 1
                continue
            self.seen.add(identity)
            if semantic is not None:
                self.semantic.add(semantic)
            self.statuses[str(status)] += 1
            if group_field:
                self.groups[group] += 1
                self.last_group = group
            if any(_field(record, name, object()) not in values for name, values in allowed.items()):
                self.unexpected += 1

    def snapshot(self, now: float, expected: int | None) -> dict:
        valid_statuses = set(self.config.get("valid_statuses", ("ok",)))
        excluded_statuses = set(self.config.get("excluded_statuses", ()))
        expected_groups = self.config.get("expected_by_group", {})
        breakdown = [
            {
                "label": str(group),
                "saved": saved,
                "expected": int(expected_groups.get(str(group), 0)),
                "excluded": 0,
                "rate_per_second": None,
                "eta_seconds": None,
            }
            for group, saved in sorted(self.groups.items(), key=lambda item: str(item[0]))
        ]
        for group, group_expected in expected_groups.items():
            if not any(row["label"] == str(group) for row in breakdown):
                breakdown.append(
                    {
                        "label": str(group),
                        "saved": 0,
                        "expected": int(group_expected),
                        "excluded": 0,
                        "rate_per_second": None,
                        "eta_seconds": None,
                    }
                )
        return {
            "saved": self.count,
            "expected": expected,
            "valid": sum(self.statuses[name] for name in valid_statuses),
            "excluded": sum(self.statuses[name] for name in excluded_statuses),
            "statuses": dict(self.statuses),
            "duplicates": self.duplicates,
            "malformed": self.reader.malformed + self.schema_malformed,
            "unexpected": self.unexpected,
            "partial_write": bool(self.reader.pending),
            "last_save_age_seconds": (
                max(0, now - self.reader.mtime) if self.reader.mtime and self.count else None
            ),
            "last": {"group": self.last_group} if self.last_group is not None else None,
            "breakdown": breakdown,
            "windows": [],
            "breakdown_title": self.config.get("group_label", "Breakdown"),
        }


class FilesystemAdapter:
    """Monitor a declarative plan without importing the experiment project."""

    queue_script: str | None = None
    tracked_scripts: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.presentation = Presentation(
            name="Filesystem",
            stages=("run",),
            labels={"run": "Run"},
            short_labels=("Run",),
            entity_heading="RUN",
            unit="items",
        )
        self._processes: dict[str, Mapping] = {}
        self._pause_file: str | None = None
        self.disk_warning_gib = 5.0

    def configure(self, plan: Mapping) -> None:
        self._validate(plan)
        tasks = [dict(task) for task in plan.get("tasks", [])]
        stages = tuple(dict.fromkeys(task.get("name", task.get("stage", "run")) for task in tasks))
        configured_stage_labels = plan.get("stage_labels", {})
        labels = {
            stage: configured_stage_labels.get(stage, stage.replace("_", " ").title())
            for stage in stages
        }
        roster = plan.get("roster", [])
        names = {run_tag({**row, "run": row["tag"]}): row.get("label", row.get("model_id", row["tag"]))
                 for row in roster}
        short_labels = plan.get("short_labels")
        self.presentation = Presentation(
            name=plan.get("name", "Filesystem"),
            stages=stages or ("run",),
            labels=labels,
            short_labels=tuple(short_labels or tuple(label[:8] for label in labels.values()))
            or ("Run",),
            entity_names=names,
            entity_heading=plan.get("entity_heading", "RUN"),
            unit=plan.get("unit", "items"),
        )
        queue_process = plan.get("queue_process", {})
        self.queue_script = queue_process.get("script")
        definitions = [queue_process, *(task.get("process", {}) for task in tasks)]
        self._processes = {
            definition["script"]: definition for definition in definitions if definition.get("script")
        }
        self.tracked_scripts = frozenset(self._processes)
        self._pause_file = plan.get("pause_file")
        self.disk_warning_gib = float(plan.get("disk_warning_gib", 5.0))

    @staticmethod
    def _validate(plan: Mapping) -> None:
        if not isinstance(plan.get("metadata", {}), Mapping):
            raise ValueError("plan.metadata must be an object")
        tmux = plan.get("tmux", {})
        if not isinstance(tmux, Mapping) or not isinstance(tmux.get("links", {}), Mapping):
            raise ValueError("plan.tmux must be an object with a links mapping")
        if any(not isinstance(k, str) or not isinstance(v, str) or not v
               for k, v in tmux.get("links", {}).items()):
            raise ValueError("plan.tmux.links must map experiment tags to target strings")
        if tmux.get("socket") is not None and not isinstance(tmux["socket"], str):
            raise ValueError("plan.tmux.socket must be a path string")
        tasks = plan.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError("plan.tasks must be a list")
        for index, task in enumerate(tasks):
            prefix = f"plan.tasks[{index}]"
            if not isinstance(task, Mapping):
                raise ValueError(f"{prefix} must be an object")
            run = task.get("model", task.get("run", task.get("group", "default")))
            stage = task.get("name", task.get("stage", "run"))
            if not isinstance(run, str) or not run or not isinstance(stage, str) or not stage:
                raise ValueError(f"{prefix} run/model and stage/name must be non-empty strings")
            directory = task.get("directory")
            if not isinstance(task.get("metadata", {}), Mapping):
                raise ValueError(f"{prefix}.metadata must be an object")
            outputs = task.get("outputs", [])
            if not isinstance(outputs, list) or not all(isinstance(p, str) and p for p in outputs):
                raise ValueError(f"{prefix}.outputs must be a list of paths or patterns")
            if task.get("tmux_session") is not None and not isinstance(task["tmux_session"], str):
                raise ValueError(f"{prefix}.tmux_session must be a target string")
            if directory is not None and not isinstance(directory, str):
                raise ValueError(f"{prefix}.directory must be a string")
            expected = task.get("expected")
            if expected is not None and (type(expected) is not int or expected < 0):
                raise ValueError(f"{prefix}.expected must be a non-negative integer")
            progress = task.get("progress")
            completion = task.get("completion")
            if (progress or completion) and not directory:
                raise ValueError(f"{prefix}.directory is required for file connectors")
            if progress is not None:
                if not isinstance(progress, Mapping) or progress.get("type") not in {
                    "jsonl",
                    "json",
                    "files",
                }:
                    raise ValueError(f"{prefix}.progress.type must be jsonl, json, or files")
                if progress["type"] in {"jsonl", "json"} and not isinstance(
                    progress.get("path"), str
                ):
                    raise ValueError(f"{prefix}.progress.path is required")
                identity = progress.get("identity", ["id"])
                if progress["type"] == "jsonl" and (
                    not isinstance(identity, list)
                    or not identity
                    or not all(isinstance(field, str) and field for field in identity)
                ):
                    raise ValueError(f"{prefix}.progress.identity must contain field names")
                max_files = progress.get("max_files", 100_000)
                if progress["type"] == "files" and (
                    type(max_files) is not int or max_files < 1
                ):
                    raise ValueError(f"{prefix}.progress.max_files must be a positive integer")
            if completion is not None and (
                not isinstance(completion, Mapping)
                or completion.get("type", "file") not in {"file", "json"}
                or not isinstance(completion.get("path"), str)
            ):
                raise ValueError(f"{prefix}.completion requires a file/json type and path")
        short_labels = plan.get("short_labels")
        stages = tuple(
            dict.fromkeys(task.get("name", task.get("stage", "run")) for task in tasks)
        )
        if short_labels is not None and (
            not isinstance(short_labels, list)
            or len(short_labels) != len(stages)
            or not all(isinstance(label, str) and label for label in short_labels)
        ):
            raise ValueError("plan.short_labels must contain one label per distinct stage")
        disk = plan.get("disk_warning_gib", 5.0)
        if isinstance(disk, bool) or not isinstance(disk, (int, float)) or not math.isfinite(disk) or disk < 0:
            raise ValueError("plan.disk_warning_gib must be a finite non-negative number")

    def default_queue(self, project_root: Path) -> Path:
        return project_root

    def default_process_output(self, script: str) -> str | None:
        return self._processes.get(script, {}).get("default_output")

    def process_destination(self, script: str, command, cwd: Path) -> Path | None:
        definition = self._processes.get(script, {})
        flag = definition.get("output_flag", "--out")
        destination = command_option(command, flag) or definition.get("default_output")
        return resolve_path(destination, cwd) if destination else None

    def task_directory(self, task: Mapping, project_root: Path) -> Path | None:
        directory = task.get("directory")
        return resolve_path(directory, project_root) if directory else None

    def task_log(self, task: Mapping, queue: Path, path: Path | None) -> Path:
        configured = task.get("log")
        if configured:
            base = path or queue
            return resolve_path(configured, base)
        return queue / f'{task["model"]}_{task["name"]}.log'

    def pause_requested(self, queue: Path) -> bool:
        return bool(self._pause_file and resolve_path(self._pause_file, queue).exists())

    def expected(self, task: Mapping, progress: Mapping | None) -> int | None:
        if progress and progress.get("expected") is not None:
            return int(progress["expected"])
        value = task.get("expected")
        return int(value) if value is not None else None

    def inspect_task(self, task, path, cache: JsonCache, trackers: dict, now: float):
        warnings: list[str] = []
        progress = None
        progress_config = task.get("progress")
        expected = task.get("expected")
        if progress_config and path:
            kind = progress_config.get("type")
            relative = progress_config.get("path")
            source = resolve_path(relative, path) if relative else path
            if kind == "jsonl":
                key = (path, task["model"], task["name"])
                tracker = trackers.get(key)
                if tracker is None or tracker.path != source or tracker.config != progress_config:
                    tracker = trackers[key] = RecordTracker(source, progress_config)
                tracker.update()
                progress = tracker.snapshot(now, int(expected) if expected is not None else None)
                if tracker.reader.pending:
                    warnings.append("uncommitted final JSONL row excluded from counts")
            elif kind == "json":
                value = cache.read(source)
                current = _field(value, progress_config.get("current_field", "current"))
                total = _field(value, progress_config.get("total_field", "total"), expected)
                if type(current) is int and current >= 0 and type(total) is int and total >= 0:
                    progress = self._simple_progress(current, total)
                    if expected is not None and total != expected:
                        progress["unexpected"] += 1
                elif value is not None:
                    progress = self._simple_progress(0, int(expected or 0), malformed=1)
            elif kind == "files":
                limit = int(progress_config.get("max_files", 100_000))
                candidates = list(islice(path.glob(progress_config.get("glob", "*")), limit + 1))
                overflow = len(candidates) > limit
                matches = {item.resolve() for item in candidates[:limit] if item.is_file()}
                progress = self._simple_progress(len(matches), int(expected or 0))
                if overflow:
                    progress["unexpected"] += 1
                    warnings.append(f"file glob exceeded its {limit:,}-entry scan limit")
        completion = task.get("completion")
        marker = None
        if completion and path:
            kind = completion.get("type", "file")
            marker_path = resolve_path(completion["path"], path)
            if kind == "json":
                value = cache.read(marker_path)
                actual = _field(value, completion.get("field", "status"))
                marker = value if actual == completion.get("equals", "complete") else None
            else:
                required = tuple(resolve_path(item, path) for item in completion.get("required", ()))
                marker = (
                    {"path": str(marker_path)}
                    if marker_path.is_file() and all(item.is_file() for item in required)
                    else None
                )
        detail = task.get("detail", "")
        return progress, marker, detail, warnings

    @staticmethod
    def _simple_progress(saved: int, expected: int, malformed: int = 0) -> dict:
        return {
            "saved": saved,
            "expected": expected,
            "valid": saved,
            "excluded": 0,
            "statuses": {"ok": saved},
            "duplicates": 0,
            "malformed": malformed,
            "unexpected": max(0, saved - expected),
            "partial_write": False,
            "last_save_age_seconds": None,
            "last": None,
            "breakdown": [],
            "windows": [],
            "breakdown_title": "Breakdown",
        }

    def state(self, task, progress, *, alive=False, exit_code=None, marker=None, stale_active=False):
        expected = self.expected(task, progress)
        if progress and (
            progress["malformed"]
            or progress["duplicates"]
            or progress["unexpected"]
            or (expected is not None and progress["saved"] > expected)
        ):
            return "invalid"
        if alive:
            return "running"
        if exit_code is not None and exit_code != 0:
            return "failed"
        if expected is not None:
            count_complete = progress and progress["saved"] == expected and not progress["partial_write"]
            artifact_complete = not task.get("completion") or marker is not None
            if count_complete and artifact_complete:
                return "complete"
            if exit_code == 0:
                return "invalid"
        elif marker is not None:
            return "complete"
        elif exit_code == 0:
            return "invalid"
        if stale_active:
            return "interrupted"
        return "partial" if progress and progress["saved"] else "queued"

    def format_log(self, record: Mapping) -> str | None:
        return None
