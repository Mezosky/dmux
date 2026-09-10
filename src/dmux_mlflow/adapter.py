"""Observe explicitly selected local MLflow runs without importing its SDK."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re

from dmux.adapters.filesystem import FilesystemAdapter
from dmux.adapters.base import resolve_path
from dmux.connectors import TextCache
from .sqlite_store import SQLiteStore


STATUSES = {1: "RUNNING", 2: "SCHEDULED", 3: "FINISHED", 4: "FAILED", 5: "KILLED"}


def run_status(text: str) -> str:
    """Read MLflow's plain top-level integer status, not arbitrary YAML.

    No YAML object constructors, aliases, merges or scalar coercion are used.
    Unsupported representations fail closed instead of guessing completion.
    """
    statuses = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*:.*", line):
            raise ValueError("unsupported meta.yaml syntax; expected a flat mapping with plain keys")
        if line.startswith("status:"):
            match = re.fullmatch(r"status: *([1-5]) *(?:#.*)?", line)
            if not match:
                raise ValueError("meta.yaml status must be a plain MLflow integer enum (1–5)")
            statuses.append(STATUSES[int(match[1])])
    if len(statuses) != 1:
        raise ValueError("meta.yaml requires exactly one top-level status")
    return statuses[0]


def _relative_key(value, label):
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(f"{label} must be a relative tracker key without dot segments")


class MLflowAdapter(FilesystemAdapter):
    """Reuse generic progress/process contracts; only run metadata is specialized."""

    def __init__(self):
        super().__init__()
        self.text = TextCache()
        self.sqlite = SQLiteStore()

    def configure(self, plan: Mapping) -> None:
        super().configure({"name": "MLflow", **plan})
        for task in plan["tasks"]:
            config = task.get("mlflow", {})
            if not isinstance(config, Mapping) or set(config) - {"params", "tags", "expected_param", "database", "run_id"}:
                raise ValueError("task.mlflow accepts params, tags, expected_param, database and run_id only")
            if "database" in config or "run_id" in config:
                if any(not isinstance(config.get(key), str) or not config[key] for key in ("database", "run_id")):
                    raise ValueError("SQLite mode requires non-empty mlflow.database and mlflow.run_id strings")
                if "://" in config["database"]:
                    raise ValueError("mlflow.database must be a local file path, not a tracking URI")
            if not task.get("directory"):
                raise ValueError("MLflow tasks require the explicit local run directory")
            for kind in ("params", "tags"):
                keys = config.get(kind, [])
                if not isinstance(keys, list) or len(keys) > 16:
                    raise ValueError(f"task.mlflow.{kind} must contain at most 16 keys")
                for key in keys:
                    _relative_key(key, f"task.mlflow.{kind}")
            if "expected_param" in config:
                _relative_key(config["expected_param"], "task.mlflow.expected_param")
                if not task.get("progress"):
                    raise ValueError("expected_param requires a separate explicit progress connector")
                if task.get("expected") is not None or "total_field" in task["progress"]:
                    raise ValueError("expected_param cannot be combined with expected or total_field")

    def inspect_task(self, task, path, cache, trackers, now):
        config = task.get("mlflow", {})
        database = resolve_path(config["database"], path) if "database" in config else None
        source = "SQLite run" if database is not None else "meta.yaml"

        def value(kind, key):
            return (self.sqlite.value(database, config["run_id"], kind, key) if database is not None
                    else self.text.read(path / kind / key).strip())

        warnings = []
        status, invalid = None, False
        try:
            status = (self.sqlite.status(database, config["run_id"]) if database is not None
                      else run_status(self.text.read(path / "meta.yaml")))
        except FileNotFoundError:
            warnings.append(f"MLflow {source} not generated yet")
        except (OSError, ValueError, UnicodeError) as exc:
            invalid = True
            warnings.append(f"MLflow {source} unreadable: {exc}")
        metadata = dict(task.get("metadata", {}))
        for kind in ("params", "tags"):
            for key in config.get(kind, []):
                try:
                    metadata[f"{kind}/{key}"] = value(kind, key)
                except (OSError, ValueError, UnicodeError) as exc:
                    warnings.append(f"MLflow {kind}/{key} unreadable: {exc}")
        task["metadata"] = metadata
        if "expected_param" in config:
            try:
                expected_value = value("params", config["expected_param"])
                if not re.fullmatch(r"[0-9]+", expected_value):
                    raise ValueError("expected param must be a non-negative integer")
                task["expected"] = int(expected_value)
            except (OSError, ValueError, UnicodeError) as exc:
                invalid = True
                warnings.append(f"MLflow expected_param unreadable: {exc}")
        progress, completion, detail, generic_warnings = super().inspect_task(task, path, cache, trackers, now)
        if "expected_param" in config and progress and progress["expected"] != task.get("expected"):
            invalid = True
            warnings.append("MLflow expected_param disagrees with the progress total")
        marker = {"status": status, "invalid": invalid, "completion": completion}
        reported = f"MLflow reported {status}" if status else "MLflow status unavailable"
        if status == "RUNNING":
            reported += " (not proof of liveness; see matched PID)"
        return progress, marker, "; ".join(filter(None, (detail, reported))), [*warnings, *generic_warnings]

    def state(self, task, progress, *, alive=False, exit_code=None, marker=None, stale_active=False):
        marker = marker or {}
        if marker.get("invalid"):
            return "invalid"
        status = marker.get("status")
        completed = status == "FINISHED" and (not task.get("completion") or marker.get("completion"))
        # Require tracker completion even if an explicit progress counter has reached its total.
        state = super().state({**task, "completion": True}, progress, alive=alive,
                              exit_code=exit_code, marker=True if completed else None,
                              stale_active=stale_active or status == "RUNNING")
        if state == "invalid" or alive:
            return state
        if status in {"FAILED", "KILLED"}:
            return "failed"
        return state
