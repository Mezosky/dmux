"""Bounded read-only SELECTs against an explicitly configured SQLite database."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import time

from .connectors import open_regular


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
        raise ValueError("SQLite identifiers must contain only letters, digits and underscores")
    return '"' + value + '"'


def database_stamp(path):
    """Reject special files and WAL before SQLite can create coordination files."""
    path = Path(path)
    with open_regular(path) as handle:
        info = os.fstat(handle.fileno())
        header = handle.read(100)
    if len(header) != 100 or not header.startswith(b"SQLite format 3\0"):
        raise ValueError("not a SQLite database")
    if header[18:20] != b"\x01\x01" or Path(str(path) + "-wal").exists():
        raise ValueError("WAL databases are not supported; provide a consistent rollback-journal SQLite snapshot")
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)


class SQLiteReader:
    """Short read transactions; no schema changes, recovery, or arbitrary SQL.

    Limits apply to returned rows, decoded bytes, VM instructions and elapsed
    query time. A missing index therefore fails visibly instead of scanning an
    arbitrarily large table on the UI thread.
    """
    max_rows = 257
    max_bytes = 256 * 1024
    max_instructions = 100_000
    max_seconds = .1

    def read(self, path, *, table, columns, where, order_by=(), limit=1):
        if not 1 <= limit <= self.max_rows:
            raise ValueError("SQLite row limit exceeded")
        columns = tuple(dict.fromkeys(columns))
        if not columns or len(columns) > 16 or len(where) > 16 or len(order_by) > 4:
            raise ValueError("SQLite query exceeds column/filter limits")
        query = "SELECT " + ", ".join(map(identifier, columns)) + " FROM " + identifier(table)
        if where:
            query += " WHERE " + " AND ".join(identifier(key) + " IS ?" for key in where)
        if order_by:
            query += " ORDER BY " + ", ".join(identifier(key) + " DESC" for key in order_by)
        query += " LIMIT ?"
        path = Path(path).resolve()
        stamp = database_stamp(path)
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.02)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65_536)
            # Only fixed SELECT/READ operations are needed; deny attachments,
            # pragmas, writes, schema operations and user-defined functions.
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}
            connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
            deadline = time.monotonic() + self.max_seconds
            instructions = 0

            def budget():
                nonlocal instructions
                instructions += 1000
                return int(instructions > self.max_instructions or time.monotonic() > deadline)

            connection.set_progress_handler(budget, 1000)
            values = connection.execute(query, (*where.values(), limit))
            total = 0
            rows = []
            for row in values:
                total += sum(len(str(value).encode("utf-8")) for value in row)
                if total > self.max_bytes:
                    raise ValueError("SQLite result exceeds 256 KiB")
                rows.append(dict(zip(columns, row)))
            if database_stamp(path) != stamp:
                raise ValueError("SQLite database changed during the read; retry on refresh")
            return rows
        except sqlite3.Error as exc:
            raise ValueError(f"SQLite read unavailable: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()
