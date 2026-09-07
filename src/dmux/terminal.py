"""Terminal mode handling with guaranteed restoration."""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import select
import sys
import time


@contextmanager
def keyboard():
    """Yield a non-blocking key reader and always restore terminal settings."""

    if not sys.stdin.isatty() or os.name != "posix":
        yield lambda: ""
        return
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        pending, escaped_at = "", None

        def read():
            nonlocal pending, escaped_at
            if select.select([descriptor], [], [], 0)[0]:
                pending += os.read(descriptor, 32).decode(errors="ignore")
            output = ""
            while pending:
                if pending.startswith("\x1b"):
                    escaped_at = time.monotonic() if escaped_at is None else escaped_at
                    match = re.match(r"\x1b\[[0-?]*[ -/]*[@-~]", pending)
                    if match:
                        output += {"\x1b[A": "k", "\x1b[B": "j"}.get(match[0], "")
                        pending = pending[len(match[0]) :]
                        escaped_at = None
                        continue
                    if time.monotonic() - escaped_at < 0.05:
                        break
                output += pending[0]
                pending, escaped_at = pending[1:], None
            return output

        yield read
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)

