"""Adapter discovery, including third-party ``dmux.adapters`` entry points."""
from __future__ import annotations

from importlib.metadata import entry_points

from .adapters.filesystem import FilesystemAdapter
from .adapters.supergpqa import SuperGPQAAdapter


BUILTINS = {
    "filesystem": FilesystemAdapter,
    "supergpqa": SuperGPQAAdapter,
}


def adapter_names() -> tuple[str, ...]:
    plugins = {entry.name for entry in entry_points(group="dmux.adapters")}
    return tuple(sorted(set(BUILTINS) | plugins))


def load_adapter(name: str):
    if name in BUILTINS:
        return BUILTINS[name]()
    matches = [entry for entry in entry_points(group="dmux.adapters") if entry.name == name]
    if len(matches) != 1:
        available = ", ".join(adapter_names())
        raise ValueError(f"Unknown adapter {name!r}. Available adapters: {available}")
    return matches[0].load()()

