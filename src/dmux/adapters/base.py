"""Adapter contracts shared by the monitor and visual layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Presentation:
    name: str
    stages: tuple[str, ...]
    labels: Mapping[str, str]
    short_labels: tuple[str, ...]
    entity_names: Mapping[str, str] = field(default_factory=dict)
    entity_heading: str = "RUN"
    unit: str = "evaluations"

    def stage_label(self, name: str) -> str:
        return self.labels.get(name, name.replace("_", " ").title())

    def entity_name(self, tag: str) -> str:
        return self.entity_names.get(tag, tag)


class ExperimentAdapter(Protocol):
    """The interpretation layer for one experiment filesystem schema."""

    presentation: Presentation
    queue_script: str | None
    tracked_scripts: frozenset[str]
    disk_warning_gib: float

    def configure(self, plan: Mapping) -> None: ...

    def default_queue(self, project_root: Path) -> Path: ...

    def process_destination(self, script: str, command: Sequence[str], cwd: Path) -> Path | None: ...

    def task_directory(self, task: Mapping, project_root: Path) -> Path | None: ...

    def inspect_task(self, task: Mapping, path: Path | None, cache, trackers, now: float): ...

    def task_log(self, task: Mapping, queue: Path, path: Path | None) -> Path: ...

    def expected(self, task: Mapping, progress: Mapping | None) -> int | None: ...

    def state(self, task: Mapping, progress, **facts) -> str: ...

    def format_log(self, record: Mapping) -> str | None: ...

    def pause_requested(self, queue: Path) -> bool: ...


def resolve_path(value: str | Path, base: str | Path) -> Path:
    """Resolve a project-produced relative path against an explicit base."""

    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (Path(base) / path).resolve()


def command_option(command: Sequence[str], flag: str) -> str | None:
    try:
        return command[command.index(flag) + 1]
    except (ValueError, IndexError):
        return None
