"""Explicit project and result locations, independent of the monitor checkout."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .adapters.base import resolve_path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    results: Path
    tmux_session: str | None = None


def project_paths(plan: Mapping, root: Path, results_dir=None) -> dict[str | None, ProjectPaths]:
    """A CLI result override applies to the default project only.

    Named projects keep their own roots and output locations. Omitting a results
    location preserves the original project-relative directory behavior.
    """
    def location(value, base, field):
        if not isinstance(value, (str, Path)) or not str(value):
            raise ValueError(f"{field} must be a non-empty path")
        return resolve_path(value, base)

    default_results = results_dir if results_dir is not None else plan.get("results_dir", root)
    paths: dict[str | None, ProjectPaths] = {None: ProjectPaths(root, location(default_results, root, "results_dir"))}
    projects = plan.get("projects", {})
    if not isinstance(projects, Mapping):
        raise ValueError("projects must map project IDs to root/results_dir objects")
    for name, project in projects.items():
        if not isinstance(name, str) or not name or "/" in name or not isinstance(project, Mapping):
            raise ValueError("each project needs a non-empty ID without '/' and a settings object")
        project_root = location(project.get("root", root), root, f"projects.{name}.root")
        results = location(project.get("results_dir", project_root), project_root,
                           f"projects.{name}.results_dir")
        if not isinstance(project.get("metadata", {}), Mapping):
            raise ValueError(f"projects.{name}.metadata must be an object")
        session = project.get("tmux_session")
        if session is not None and (not isinstance(session, str) or not session):
            raise ValueError(f"projects.{name}.tmux_session must be a non-empty target")
        paths[name] = ProjectPaths(project_root, results, session)
    for task in plan["tasks"]:
        if not isinstance(task, Mapping):
            raise ValueError("tasks must contain objects")
        name = task.get("project")
        if name is not None and (not isinstance(name, str) or name not in paths):
            raise ValueError(f"Unknown task project {name!r}")
    return paths


def run_tag(record: Mapping) -> str:
    run = record.get("experiment", record.get("run", record.get("model", record.get("group", "default"))))
    return f'{record["project"]}/{run}' if record.get("project") else run


def experiment_roster(plan: Mapping) -> list:
    """Read the generic experiment catalog, accepting the earlier roster alias."""
    entries = plan.get("experiments", plan.get("roster", []))
    if not isinstance(entries, list) or any(
        not isinstance(row, Mapping) or not isinstance(row.get("tag"), str) or not row["tag"]
        for row in entries
    ):
        raise ValueError("plan.experiments must be a list of objects with non-empty tags")
    return entries
