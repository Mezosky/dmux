"""Versioned JSON exports, independent of the dashboard's compatibility objects."""
from __future__ import annotations

from copy import deepcopy


def export_snapshot(snapshot: dict, *, version: int = 2) -> dict:
    """Copy a monitor snapshot using canonical v2 names or legacy v1 aliases."""
    if version not in (1, 2):
        raise ValueError("Supported snapshot schema versions: 1, 2")
    result = deepcopy(snapshot)
    result["schema_version"] = version
    if version == 2:
        for alias in ("models", "queue"):
            result.pop(alias, None)
        for task in [*result["tasks"], *([result["active"]] if result.get("active") else [])]:
            for alias in ("model", "name"):
                task.pop(alias, None)
    return result


def export_home(projects: list, experiments: list, warning: str | None, *, version: int = 2) -> dict:
    """Home is a separate state-only report; v1 retains its original shape."""
    if version not in (1, 2):
        raise ValueError("Supported home schema versions: 1, 2")
    result: dict = deepcopy({"projects": projects, "experiments": experiments, "warning": warning})
    if version == 2:
        result["schema_version"] = version
    return result
