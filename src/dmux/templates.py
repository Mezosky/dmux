"""Bounded expansion of explicitly configured run-directory globs."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import fnmatch
import os
from pathlib import Path

from .projects import project_paths
from .plan_schema import validate_plan_schema


def expand_templates(plan, root, results_dir=None):
    templates = plan.get("task_templates", [])
    if not isinstance(templates, list) or len(templates) > 16:
        raise ValueError("task_templates must contain at most 16 templates")
    if not templates:
        return plan
    validate_plan_schema(plan)
    locations = project_paths(plan, root, results_dir)
    tasks = list(plan["tasks"])
    for template in templates:
        if not isinstance(template, Mapping) or set(template) - {"glob", "task", "tag_prefix", "max_matches", "max_scan"}:
            raise ValueError("task template accepts glob, task, tag_prefix, max_matches and max_scan")
        pattern = template.get("glob")
        if (not isinstance(pattern, str) or not pattern or Path(pattern).is_absolute()
                or len(pattern.split("/")) > 8 or any(part in {"", ".", "..", "**"} for part in pattern.split("/"))):
            raise ValueError("template glob must have 1–8 relative segments; recursive ** and dot segments are not allowed")
        task = template.get("task")
        if not isinstance(task, Mapping) or any(key in task for key in ("directory", "experiment", "model", "run", "group")):
            raise ValueError("template.task must be an object; directory and experiment identity are supplied by expansion")
        prefix = template.get("tag_prefix", "sweep")
        if not isinstance(prefix, str) or not prefix or prefix.endswith("/"):
            raise ValueError("template.tag_prefix must be a non-empty string without a trailing slash")
        maximum, budget = template.get("max_matches", 100), template.get("max_scan", 1000)
        if type(maximum) is not int or not 1 <= maximum <= 1000 or type(budget) is not int or not 1 <= budget <= 10_000:
            raise ValueError("template limits require max_matches 1–1000 and max_scan 1–10000")
        project = task.get("project")
        if not isinstance(project, (str, type(None))) or project not in locations:
            raise ValueError(f"Unknown template project {project!r}")
        base = locations[project].results
        candidates = [base]
        visited = 0
        for segment in pattern.split("/"):
            matches = []
            for parent in candidates:
                try:
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            visited += 1
                            if visited > budget:
                                raise ValueError(f"template {pattern!r} exceeded max_scan={budget}; narrow the glob")
                            if (not entry.name.startswith(".") or segment.startswith(".")) and fnmatch.fnmatchcase(entry.name, segment):
                                if entry.is_dir(follow_symlinks=False):
                                    matches.append(Path(entry.path))
                except FileNotFoundError:
                    continue
            candidates = sorted(matches)
        if len(candidates) > maximum:
            raise ValueError(f"template {pattern!r} exceeded max_matches={maximum}; narrow the glob")
        for path in candidates:
            tasks.append({**deepcopy(task), "directory": str(path),
                          "experiment": prefix + "/" + path.relative_to(base).as_posix()})
    return {**plan, "tasks": tasks}
