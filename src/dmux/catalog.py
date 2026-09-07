"""Explicit per-user project registration; never stores or mutates results."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from .adapters.base import resolve_path
from .adapters.filesystem import FilesystemAdapter
from .connectors import JsonCache, open_regular
from .projects import project_paths


class CatalogError(ValueError):
    pass


def user_directory(kind: str) -> Path:
    defaults = {"config": ".config", "state": ".local/state", "cache": ".cache"}
    configured = os.environ.get(f"XDG_{kind.upper()}_HOME", "")
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / defaults[kind]
    return base / "dmux"


def atomic_json(path: Path, value) -> None:
    """Replace one owned settings file atomically; never follow a target symlink."""
    if path.is_symlink():
        raise CatalogError(f"Refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".dmux-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ProjectCatalog:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else user_directory("config") / "projects.json"

    def read(self) -> list[dict]:
        try:
            with open_regular(self.path) as handle:
                raw = handle.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("registration list exceeds 1 MiB")
            document = json.loads(raw)
            if not isinstance(document, dict) or document.get("version") != 1:
                raise ValueError("unsupported registration format (expected version 1)")
            entries = document.get("projects")
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError("projects must be a list of at most 1,000 registrations")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("project registration must be an object")
                for key in ("id", "name", "plan_dir", "project_root"):
                    if not isinstance(entry.get(key), str) or not entry[key]:
                        raise ValueError(f"registration requires {key}")
                for key in ("plan_dir", "project_root", "results_dir"):
                    if entry.get(key) is not None and (
                        not isinstance(entry[key], str) or not Path(entry[key]).is_absolute()
                    ):
                        raise ValueError(f"registration {key} must be an absolute path")
            for key in ("id", "name", "plan_dir"):
                if len({e[key] for e in entries}) != len(entries):
                    raise ValueError(f"duplicate registration {key}")
            return entries
        except FileNotFoundError:
            if self.path.is_symlink():
                raise CatalogError(f"Dangling registration symlink: {self.path}")
            return []
        except (OSError, ValueError, TypeError) as exc:
            raise CatalogError(f"Cannot read {self.path}: {exc}. Existing registrations were not changed.") from exc

    @contextmanager
    def _locked(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CatalogError("Another dmux is updating registrations; try again.")
                    time.sleep(.02)
            yield
        finally:
            os.close(descriptor)

    def add(self, target, *, name=None, project_root=None, results_dir=None) -> dict:
        target = Path(target).expanduser().resolve()
        directory = target.parent if target.name == "plan.json" else FilesystemAdapter().default_queue(target)
        path = directory / "plan.json"
        if not path.is_file():
            raise CatalogError(f"No plan.json found at {directory}. Run dmux init first, or give an exact plan path.")
        cache = JsonCache()
        plan = cache.read(path)
        if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list) or cache.warnings:
            raise CatalogError("Plan must be readable JSON with a tasks list.")
        FilesystemAdapter().configure(plan)
        fallback = target if target.is_dir() and target != directory else directory
        root = (Path(project_root).expanduser().resolve() if project_root else
                resolve_path(plan.get("project_root", fallback), directory))
        locations = project_paths(plan, root, results_dir)
        alias = name or re.sub(r"[^a-zA-Z0-9_.-]+", "-", root.name).strip("-") or "project"
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", alias):
            raise CatalogError("Project name must be 1–80 letters, digits, dots, underscores or hyphens, starting with a letter/digit.")
        with self._locked():
            entries = self.read()
            existing = next((e for e in entries if e["plan_dir"] == str(directory)), None)
            if existing and name is None:
                alias = existing["name"]
            if any(e["name"] == alias and e is not existing for e in entries):
                raise CatalogError(f"Name {alias!r} is already registered; supply a different --name.")
            entry = {"id": existing["id"] if existing else uuid.uuid4().hex,
                     "name": alias, "plan_dir": str(directory), "project_root": str(root),
                     "results_dir": str(locations[None].results) if results_dir is not None
                     else existing.get("results_dir") if existing else None}
            if existing:
                entries[entries.index(existing)] = entry
            else:
                entries.append(entry)
            if len(entries) > 1000:
                raise CatalogError("Registration limit reached (1,000 projects).")
            atomic_json(self.path, {"version": 1, "projects": entries})
        return entry

    def remove(self, selector: str) -> dict:
        with self._locked():
            entries = self.read()
            matches = [e for e in entries if selector in (e["id"], e["name"])]
            if len(matches) != 1:
                raise CatalogError("Supply one exact, unambiguous registered project name or ID.")
            entry = matches[0]
            atomic_json(self.path, {"version": 1, "projects": [e for e in entries if e is not entry]})
        return entry


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="dmux projects")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="Register an existing project or plan")
    add.add_argument("path", type=Path)
    add.add_argument("--name")
    add.add_argument("--project-root", type=Path)
    add.add_argument("--results-dir", type=Path)
    remove = commands.add_parser("remove", help="Unregister only; never stop jobs or delete results")
    remove.add_argument("name")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = ProjectCatalog()
        if args.command == "add":
            entry = catalog.add(args.path, name=args.name, project_root=args.project_root, results_dir=args.results_dir)
            print(f'Registered {entry["name"]}. Run dmux from anywhere to browse it.')
        elif args.command == "remove":
            entry = catalog.remove(args.name)
            print(f'Unregistered {entry["name"]}. Jobs, tmux sessions and result files are unchanged.')
        else:
            entries = catalog.read()
            if args.json:
                print(json.dumps({"version": 1, "projects": entries}, indent=2))
            else:
                for entry in entries:
                    print(f'{entry["name"]}  {entry["plan_dir"]}  [{entry["id"]}]')
                if not entries:
                    print("No projects registered. Use dmux add /path/to/project.")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
