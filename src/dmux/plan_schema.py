"""Packaged editor schema and offline structural validation for doctor."""
from __future__ import annotations

from importlib.resources import files
import json


def validate_plan_schema(plan: object) -> None:
    """Use only bundled/local schema references; never fetch a producer's $schema."""
    from jsonschema import Draft202012Validator

    schema = json.loads(files("dmux").joinpath("schemas/plan.schema.json").read_text(encoding="utf-8"))
    error = next(Draft202012Validator(schema).iter_errors(plan), None)
    if error is not None:
        path = ".".join(map(str, error.absolute_path)) or "plan"
        raise ValueError(f"{path}: {error.message}")
