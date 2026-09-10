"""Bounded, read-only connectors for experiment-generated files.

Connectors do not know what a row means. Adapters apply experiment-specific
identity, completion, and integrity rules on top of these primitives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat as stat_mode
from typing import Callable, Mapping


@contextmanager
def open_regular(path, mode="rb", **kwargs):
    """Never block on a producer-supplied pipe or deserialize a device."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, mode, **kwargs) as handle:
        if not stat_mode.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"Not a regular file: {path}")
        yield handle


class JsonCache:
    """Read JSON while tolerating brief non-atomic rewrites.

    The last valid document is retained when a writer temporarily leaves an
    incomplete file. Missing files are different: their cached value is dropped.
    """

    def __init__(self, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        self.cache: dict[Path, tuple[tuple[int, int, int], object]] = {}
        self.warnings: dict[Path, str] = {}
        self.max_bytes = max_bytes

    def read(self, path: str | Path, default=None):
        path = Path(path)
        try:
            stat = path.stat()
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if path in self.cache and self.cache[path][0] == stamp:
                return self.cache[path][1]
            if stat.st_size > self.max_bytes:
                raise ValueError(f"JSON document exceeds {self.max_bytes} bytes")
            with open_regular(path, "r", encoding="utf-8") as handle:
                raw = handle.read(self.max_bytes + 1)
            if len(raw.encode("utf-8")) > self.max_bytes:
                raise ValueError(f"JSON document exceeds {self.max_bytes} bytes")
            value = json.loads(raw)
            self.cache[path] = (stamp, value)
            self.warnings.pop(path, None)
            return value
        except FileNotFoundError:
            self.cache.pop(path, None)
            self.warnings.pop(path, None)
            return default
        except (OSError, ValueError, UnicodeError) as exc:
            self.warnings[path] = (
                f"{path.name}: temporarily unreadable ({type(exc).__name__})"
            )
            return self.cache.get(path, (None, default))[1]


class TextCache:
    """Bounded UTF-8 documents; errors evict old data instead of implying success."""

    def __init__(self, *, max_bytes=65_536, max_entries=128):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.cache = OrderedDict()

    def read(self, path: str | Path) -> str:
        path = Path(path)
        try:
            with open_regular(path) as handle:
                info = os.fstat(handle.fileno())
                stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                if path in self.cache and self.cache[path][0] == stamp:
                    self.cache.move_to_end(path)
                    return self.cache[path][1]
                if info.st_size > self.max_bytes:
                    raise ValueError(f"text document exceeds {self.max_bytes} bytes")
                raw = handle.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ValueError(f"text document exceeds {self.max_bytes} bytes")
                value = raw.decode("utf-8")
            self.cache[path] = (stamp, value)
            self.cache.move_to_end(path)
            while len(self.cache) > self.max_entries:
                self.cache.popitem(last=False)
            return value
        except (OSError, ValueError, UnicodeError):
            self.cache.pop(path, None)
            raise


@dataclass
class IncrementalJsonlReader:
    """Read only newline-committed JSON objects appended since the last poll.

    Rotation, replacement, and truncation reset the cursor. An unterminated
    final line remains pending and is never returned as committed data.
    """

    path: Path
    identity: tuple[int, int] | None = None
    offset: int = 0
    pending: bytes = b""
    malformed: int = 0
    mtime: float | None = None
    max_bytes_per_read: int = 16 * 1024 * 1024

    def reset(self) -> None:
        self.identity = None
        self.offset = 0
        self.pending = b""
        self.malformed = 0
        self.mtime = None

    def read(self) -> tuple[list[dict], bool]:
        records: list[dict] = []
        reset = False
        try:
            with open_regular(self.path) as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity != identity or stat.st_size < self.offset:
                    self.reset()
                    self.identity = identity
                    reset = True
                self.mtime = stat.st_mtime
                handle.seek(self.offset)
                remaining = self.max_bytes_per_read
                while remaining and (chunk := handle.read(min(1024 * 1024, remaining))):
                    self.offset += len(chunk)
                    remaining -= len(chunk)
                    lines = (self.pending + chunk).split(b"\n")
                    self.pending = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                            if not isinstance(record, dict):
                                raise TypeError("JSONL record must be an object")
                        except (ValueError, TypeError, UnicodeError):
                            self.malformed += 1
                            continue
                        records.append(record)
        except FileNotFoundError:
            reset = bool(self.identity is not None or self.offset or self.pending)
            self.reset()
        return records, reset


@dataclass(frozen=True)
class FileArtifact:
    """A completion artifact and any files required to validate it."""

    marker: Path
    required: tuple[Path, ...] = field(default_factory=tuple)

    def inspect(self, cache: JsonCache) -> object | None:
        marker = cache.read(self.marker)
        if not marker or any(not path.is_file() for path in self.required):
            return None
        return marker


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def head_text(path, n=5, *, max_bytes=4096, max_line_length=180):
    """Return a sanitized, bounded file beginning without parsing its format."""
    if path is None or n <= 0:
        return []
    if max_bytes <= 0:
        raise ValueError('head_text requires a positive byte limit')
    try:
        with open_regular(Path(path)) as handle:
            raw = handle.read(max_bytes).decode('utf-8', errors='replace')
    except OSError:
        return []
    output = []
    for line in raw.splitlines():
        line = ANSI_ESCAPE.sub('', line)
        line = ''.join(c for c in line if c.isprintable() or c == '\t').strip()
        if line:
            output.append(line[:max_line_length])
        if len(output) == n:
            break
    return output


def tail_log(
    path: str | Path | None,
    n: int = 3,
    *,
    formatter: Callable[[Mapping], str | None] | None = None,
    max_bytes: int = 16_384,
    max_line_length: int = 220,
) -> list[str]:
    """Return a sanitized, bounded tail without following or modifying a log."""

    if path is None:
        return []
    try:
        with open_regular(Path(path)) as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            lines = handle.read(max_bytes).decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

    output: list[str] = []
    for raw in lines[-20:]:
        line = ANSI_ESCAPE.sub("", raw)
        line = "".join(c for c in line if c.isprintable() or c == "\t").strip()
        if not line:
            continue
        if formatter:
            try:
                record = json.loads(line)
                formatted = formatter(record) if isinstance(record, dict) else None
                if formatted is not None:
                    line = formatted
            except (ValueError, KeyError, TypeError):
                pass
        output.append(line[:max_line_length])
    return output[-n:]
