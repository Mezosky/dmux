"""Terminal mode handling with guaranteed restoration."""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import select
import signal
import threading
import sys
import time


RESUME = "\x00"  # Non-printable redraw event; never a navigation or action key.
UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"


class TerminalSignal(SystemExit):
    """Exit requested by a host signal; project navigation must not swallow it."""


@contextmanager
def keyboard():
    """Yield a non-blocking key reader and always restore terminal settings."""

    if not sys.stdin.isatty() or os.name != "posix":
        yield lambda: []
        return
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    output_stream = sys.stdout  # Capture before Rich redirects writes through its renderer.
    handlers = {}
    resumed = False

    def handle_signal(number, frame):
        nonlocal resumed
        if number != signal.SIGTSTP:
            # Unwind Live before keyboard's finally restores termios.
            raise TerminalSignal(128 + number)
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        output_stream.write("\x1b[?1049l\x1b[?25h")
        output_stream.flush()
        # Stop only dmux, including in an orphaned process group. SIGCONT
        # returns here; do not suspend or signal any experiment processes.
        os.kill(os.getpid(), signal.SIGSTOP)
        tty.setcbreak(descriptor)
        output_stream.write("\x1b[?1049h\x1b[?25l")
        output_stream.flush()
        resumed = True

    try:
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP):
                handlers[number] = signal.getsignal(number)
                signal.signal(number, handle_signal)
        tty.setcbreak(descriptor)
        pending, escaped_at = "", None

        def read():
            nonlocal pending, escaped_at, resumed
            if select.select([descriptor], [], [], 0)[0]:
                pending += os.read(descriptor, 32).decode(errors="ignore")
            output = [RESUME] if resumed else []
            resumed = False
            while pending:
                if pending.startswith("\x1b"):
                    escaped_at = time.monotonic() if escaped_at is None else escaped_at
                    match = re.match(r"\x1b\[[0-?]*[ -/]*[@-~]", pending)
                    if match:
                        if match[0] in {UP, DOWN, LEFT, RIGHT}:
                            output.append(match[0])
                        pending = pending[len(match[0]) :]
                        escaped_at = None
                        continue
                    if time.monotonic() - escaped_at < 0.05:
                        break
                output.append(pending[0])
                pending, escaped_at = pending[1:], None
            return output

        yield read
    finally:
        try:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)

