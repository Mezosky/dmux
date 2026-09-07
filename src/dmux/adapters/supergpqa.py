"""Adapter for the original SuperGPQA production queue schema."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import math
from pathlib import Path
import time
from typing import Mapping

from ..connectors import FileArtifact, IncrementalJsonlReader, JsonCache
from .base import Presentation, command_option, resolve_path


STAGES = (
    "train",
    "calibration",
    "fit_supergpqa_heads",
    "test",
    "repeat_test_baseline",
    "audit_repeat",
    "evaluate_frozen_heads",
    "screen",
)
LABELS = {
    "train": "Collect training data",
    "calibration": "Collect calibration data",
    "fit_supergpqa_heads": "Fit SuperGPQA detectors",
    "test": "Collect held-out test",
    "repeat_test_baseline": "Repeat test baselines",
    "audit_repeat": "Audit determinism",
    "evaluate_frozen_heads": "Evaluate frozen detectors",
    "screen": "0/1/3/5-shot scaling",
}
MODEL_NAMES = {
    "e4b": "Gemma4 E4B",
    "qwen3_4b": "Qwen3 4B",
    "gemma4_12b": "Gemma4 12B",
    "gemma3_4b": "Gemma3 4B",
    "llama31_8b": "Llama3.1 8B",
    "phi4": "Phi-4",
    "ministral8b": "Ministral 8B",
    "qwen3_14b": "Qwen3 14B",
    "qwen38_27b": "Qwen3.8 27B",
    "llama32_11b": "Llama3.2 11B",
}

SUPERGPQA = Presentation(
    name="SuperGPQA",
    stages=STAGES,
    labels=LABELS,
    short_labels=("Train", "Cal", "Fit", "Test", "Repeat", "Audit", "Eval", "Shots"),
    entity_names=MODEL_NAMES,
    entity_heading="MODEL",
    unit="evaluations",
)


@dataclass
class RowTracker:
    """Count committed unique SuperGPQA evaluations through a generic cursor."""

    path: Path
    reader: IncrementalJsonlReader = field(init=False)
    seen: set[str] = field(default_factory=set)
    semantic: set[tuple] = field(default_factory=set)
    targets: set[str] = field(default_factory=set)
    statuses: Counter = field(default_factory=Counter)
    lengths: Counter = field(default_factory=Counter)
    arms: Counter = field(default_factory=Counter)
    invalid_lengths: Counter = field(default_factory=Counter)
    schema_malformed: int = 0
    duplicates: int = 0
    unexpected: int = 0
    last: dict | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=180))

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.reader = IncrementalJsonlReader(self.path)

    @property
    def identity(self):
        return self.reader.identity

    @property
    def offset(self):
        return self.reader.offset

    @property
    def pending(self):
        return self.reader.pending

    @property
    def mtime(self):
        return self.reader.mtime

    @property
    def malformed(self):
        return self.reader.malformed + self.schema_malformed

    @property
    def count(self):
        return len(self.seen)

    def reset(self) -> None:
        self.reader.reset()
        self._reset_counts()

    def _reset_counts(self) -> None:
        self.seen.clear()
        self.semantic.clear()
        self.targets.clear()
        self.statuses.clear()
        self.lengths.clear()
        self.arms.clear()
        self.invalid_lengths.clear()
        self.schema_malformed = 0
        self.duplicates = 0
        self.unexpected = 0
        self.last = None
        self.history.clear()

    def update(self, manifest=None, now=None) -> None:
        now = time.time() if now is None else now
        rows, reset = self.reader.read()
        if reset:
            self._reset_counts()
        allowed_targets = set(manifest["target_uuids"]) if manifest else None
        allowed_grid = {tuple(cell) for cell in manifest["grid"]} if manifest else None
        for row in rows:
            try:
                cid, uid = row["cell_id"], row["target_uuid"]
                arm, length, replicate = row["arm"], row["length"], row["replicate"]
                status = row["status"]
                if not all(isinstance(v, str) for v in (cid, uid, arm, status)):
                    raise ValueError("invalid string field")
                if type(length) is not int or type(replicate) is not int:
                    raise ValueError("invalid integer field")
            except (ValueError, KeyError, TypeError):
                self.schema_malformed += 1
                continue
            semantic = (uid, arm, length, replicate)
            if cid in self.seen or semantic in self.semantic:
                self.duplicates += 1
                continue
            self.seen.add(cid)
            self.semantic.add(semantic)
            self.targets.add(uid)
            self.statuses[status] += 1
            self.lengths[length] += 1
            self.arms[(arm, length)] += 1
            if status != "ok":
                self.invalid_lengths[length] += 1
            if allowed_targets is not None and (
                uid not in allowed_targets or (arm, length, replicate) not in allowed_grid
            ):
                self.unexpected += 1
            self.last = {"arm": arm, "length": length, "status": status}
        self.history.append((now, dict(self.lengths)))
        while len(self.history) > 2 and self.history[1][0] < now - 120:
            self.history.popleft()

    def rate(self, length: int) -> float | None:
        if len(self.history) < 2:
            return None
        start, end = self.history[0], self.history[-1]
        elapsed = end[0] - start[0]
        delta = end[1].get(length, 0) - start[1].get(length, 0)
        if elapsed < 15 or delta < 5:
            return None
        return delta / elapsed

    def snapshot(self, manifest=None, now=None) -> dict:
        now = time.time() if now is None else now
        expected = Counter()
        if manifest:
            for _, length, _ in manifest["grid"]:
                expected[length] += len(manifest["target_uuids"])
        current = self.last["length"] if self.last else None
        windows = []
        for length in sorted(set(expected) | set(self.lengths)):
            rate = self.rate(length) if length == current else None
            remaining = max(0, expected[length] - self.lengths[length])
            windows.append(
                {
                    "length": length,
                    "label": window_name(length),
                    "saved": self.lengths[length],
                    "expected": expected[length],
                    "excluded": self.invalid_lengths[length],
                    "rate_per_second": rate,
                    "eta_seconds": remaining / rate if rate and expected[length] else None,
                }
            )
        return {
            "saved": self.count,
            "unique_targets": len(self.targets),
            "expected_targets": len(manifest["target_uuids"]) if manifest else None,
            "valid": self.statuses["ok"],
            "excluded": self.statuses["invalid_context"],
            "statuses": dict(self.statuses),
            "duplicates": self.duplicates,
            "malformed": self.malformed,
            "unexpected": self.unexpected,
            "partial_write": bool(self.pending),
            "last_save_age_seconds": (
                max(0, now - self.mtime) if self.mtime and self.count else None
            ),
            "last": self.last,
            "windows": windows,
            "breakdown": windows,
            "breakdown_title": "Context windows",
        }


def window_name(length: int) -> str:
    return "0" if length == 0 else f"{length // 1000}K"


def task_state(task, progress, *, alive=False, exit_code=None, marker=None, stale_active=False):
    expected = task.get("expected_cells")
    if progress and (
        progress["malformed"]
        or progress["duplicates"]
        or progress["unexpected"]
        or progress["saved"] > (expected or math.inf)
    ):
        return "invalid"
    if alive:
        return "running"
    if exit_code is not None and exit_code != 0:
        return "failed"
    if expected is not None:
        complete = (
            progress
            and progress["saved"] == expected
            and progress.get("manifest_present")
            and not progress["partial_write"]
        )
        if complete and set(progress["statuses"]) <= {"ok", "invalid_context"}:
            return "complete"
        if exit_code == 0:
            return "invalid"
    elif marker is not None:
        return "complete" if marker else "invalid"
    elif exit_code == 0:
        return "invalid"
    if stale_active:
        return "interrupted"
    return "partial" if progress and progress["saved"] else "queued"


class SuperGPQAAdapter:
    presentation = SUPERGPQA
    queue_script = "run_supergpqa_production.py"
    disk_warning_gib = 12.0
    tracked_scripts = frozenset(
        {
            queue_script,
            "exp16_supergpqa_wildchat.py",
            "fit_supergpqa_detector.py",
            "check_supergpqa_repeat.py",
        }
    )

    def configure(self, plan: Mapping) -> None:
        return None

    def default_queue(self, project_root: Path) -> Path:
        return project_root / "results/supergpqa/production_v1"

    def default_process_output(self, script: str) -> str | None:
        return "results/supergpqa/production_v1" if script == self.queue_script else None

    def process_destination(self, script: str, command, cwd: Path) -> Path | None:
        destination = command_option(command, "--out") or self.default_process_output(script)
        return resolve_path(destination, cwd) if destination else None

    def task_directory(self, task: Mapping, project_root: Path) -> Path | None:
        destination = task.get("directory") or command_option(task.get("command", ()), "--out")
        return resolve_path(destination, project_root) if destination else None

    def task_log(self, task: Mapping, queue: Path, path: Path | None) -> Path:
        return queue / f'{task["model"]}_{task["name"]}.log'

    def pause_requested(self, queue: Path) -> bool:
        return (queue / "PAUSE").exists()

    def expected(self, task: Mapping, progress: Mapping | None) -> int | None:
        return task.get("expected_cells")

    def state(self, task: Mapping, progress, **facts) -> str:
        return task_state(task, progress, **facts)

    def inspect_task(
        self,
        task: Mapping,
        path: Path | None,
        cache: JsonCache,
        trackers: dict,
        now: float,
    ) -> tuple[dict | None, object | None, str, list[str]]:
        warnings: list[str] = []
        progress, marker, detail = None, None, ""
        if "expected_cells" in task and path:
            manifest = cache.read(path / "run.json")
            tracker = trackers.setdefault(path, RowTracker(path / "rows.jsonl"))
            tracker.update(manifest, now=now)
            progress = tracker.snapshot(manifest, now=now)
            progress["manifest_present"] = bool(manifest)
            if manifest and (
                manifest.get("dry_run") or manifest["expected_cells"] != task["expected_cells"]
            ):
                progress["unexpected"] += 1
            if tracker.pending:
                warnings.append("uncommitted final row excluded from counts")
        elif path:
            marker_path = path if task["name"] == "audit_repeat" else path / "complete.json"
            required: tuple[Path, ...] = ()
            if task["name"] == "fit_supergpqa_heads":
                required = (path / "heads.joblib",)
            elif task["name"] == "evaluate_frozen_heads":
                required = (path / "metrics.json", path / "predictions.json")
            marker = FileArtifact(marker_path, required).inspect(cache)
            if marker and task["name"] == "fit_supergpqa_heads":
                skipped = sum(row["status"] != "fit" for row in marker.get("status", []))
                detail = f'{marker.get("n_fitted_heads", "?")} fitted heads; {skipped} unsupported folds'
            elif marker and task["name"] == "evaluate_frozen_heads":
                detail = f'{marker.get("n_predictions", 0):,} held-out predictions'
            elif marker and task["name"] == "audit_repeat":
                detail = (
                    "Exact numerical repeat"
                    if marker.get("exactly_reproduced")
                    else "Numerical repeat differs — review"
                )
                if not marker.get("exactly_reproduced"):
                    warnings.append("determinism audit differs; review before claims")
        return progress, marker, detail, warnings

    def format_log(self, record: Mapping) -> str | None:
        if "done" in record:
            return (
                f'{record["done"]:,}/{record["total"]:,} saved  ·  '
                f'{window_name(record["length"])}  ·  {record["arm"]}  ·  {record["status"]}'
            )
        if "heldout_domain" in record:
            return (
                f'{record.get("target")}  ·  '
                f'{record.get("heldout_domain") or "pooled"}  ·  {record.get("status")}'
            )
        return None
