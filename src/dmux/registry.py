"""Adapter discovery, including third-party ``dmux.adapters`` entry points."""
from __future__ import annotations

from importlib.metadata import entry_points

from .adapters.filesystem import FilesystemAdapter


BUILTINS = {
    "filesystem": FilesystemAdapter,
}


def adapter_names() -> tuple[str, ...]:
    plugins = {entry.name for entry in entry_points(group="dmux.adapters")}
    return tuple(sorted(set(BUILTINS) | plugins))


def load_adapter(name: str):
    if name in BUILTINS:
        return BUILTINS[name]()
    matches = [entry for entry in entry_points(group="dmux.adapters") if entry.name == name]
    if len({getattr(entry, "value", id(entry)) for entry in matches}) > 1:
        raise ValueError(f"Multiple entry points register adapter {name!r}; remove the conflicting installation")
    if not matches:
        available = ", ".join(adapter_names())
        raise ValueError(f"Unknown adapter {name!r}. Available adapters: {available}")
    entry = matches[0]
    try:
        return entry.load()()
    except ImportError as exc:
        distribution = getattr(entry, "dist", None)
        package = distribution.metadata.get("Name") if distribution else None
        extras = entry.extras
        requirement = f"{package}[{','.join(extras)}]" if package and extras else package
        hint = f' Install "{requirement}" with its adapter dependencies.' if requirement else " Install its optional dependencies."
        raise ValueError(f"Adapter {name!r} unavailable: {exc}.{hint}") from exc


def adapter_statuses() -> list[tuple[str, str]]:
    """Probe only on explicit listing; normal discovery never imports plugins."""
    statuses = []
    for name in adapter_names():
        try:
            load_adapter(name)
        except Exception as exc:
            statuses.append((name, f"unavailable: {exc}"))
        else:
            statuses.append((name, "usable"))
    return statuses
