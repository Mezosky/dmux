"""Validate advisory scheduler records independently of output integrity."""
from __future__ import annotations

from .projects import run_tag


def record_key(row):
    if not isinstance(row, dict):
        raise ValueError("record must be an object")
    for field in ("experiment", "run", "model", "group", "project", "name", "stage"):
        if field == "project" and row.get(field) is None:
            continue
        if field in row and (not isinstance(row[field], str) or not row[field]):
            raise ValueError(f"{field} must be a non-empty string")
    return run_tag(row), row.get("name", row.get("stage", "run"))


def scheduler_records(status, completion):
    """Ignore malformed records with warnings; never infer process liveness."""
    warnings, exit_codes = [], {}
    active = (None, None)
    if not isinstance(status, dict):
        warnings.append("status.json: expected an object")
        status = {}
    if status.get("active") is not None:
        try:
            active = record_key(status["active"])
        except ValueError as exc:
            warnings.append(f"status.json active: {exc}")
    for source, rows in (("status.json completed_tasks", status.get("completed_tasks", [])),
                         ("completion.json", completion)):
        if not isinstance(rows, list):
            warnings.append(f"{source}: expected a list")
            continue
        for index, row in enumerate(rows):
            try:
                key = record_key(row)
                if type(row.get("returncode")) is not int:
                    raise ValueError("returncode must be an integer")
                exit_codes[key] = row["returncode"]
            except ValueError as exc:
                warnings.append(f"{source}[{index}]: {exc}")
    return active, exit_codes, warnings
