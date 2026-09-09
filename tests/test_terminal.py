"""Terminal regression tests use only owned PTYs and processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY integration")
def test_arrow_decoding_and_exception_restore(monkeypatch):
    import pty
    import termios
    from dmux.terminal import UP, DOWN, keyboard
    master, slave = pty.openpty()
    previous = termios.tcgetattr(slave)
    handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP)}
    try:
        with os.fdopen(os.dup(slave)) as stream:
            monkeypatch.setattr(sys, "stdin", stream)
            with pytest.raises(RuntimeError, match="test exception"):
                with keyboard() as read:
                    os.write(master, b"\x1b[")
                    assert read() == []
                    os.write(master, b"Ak\x1b[B")
                    assert read() == [UP, "k", DOWN]
                    raise RuntimeError("test exception")
        assert termios.tcgetattr(slave) == previous
        assert all(signal.getsignal(s) == handler for s, handler in handlers.items())
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY integration")
@pytest.mark.parametrize("mode", ["term", "hup", "suspend", "exception"])
def test_terminal_restores_on_signals_and_live_exception(mode):
    import pty
    import select
    import termios
    master, slave = pty.openpty()
    previous = termios.tcgetattr(slave)
    script = """
import time
from rich.console import Console
from rich.live import Live
from dmux.terminal import keyboard
with keyboard() as keys, Live(console=Console(), screen=True, auto_refresh=False) as live:
    while True:
        live.update('READY', refresh=True)
        if 'e' in keys():
            raise RuntimeError('owned test exception')
        time.sleep(.02)
"""
    env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    process = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave, env=env)
    output = bytearray()
    def until(predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if select.select([master], [], [], .02)[0]:
                output.extend(os.read(master, 65536))
            if predicate():
                return
        pytest.fail(f"Terminal condition not reached: {bytes(output[-1000:])!r}")
    try:
        until(lambda: b"READY" in output)
        assert termios.tcgetattr(slave) != previous
        if mode == "suspend":
            process.send_signal(signal.SIGTSTP)
            until(lambda: termios.tcgetattr(slave) == previous and b"\x1b[?1049l" in output)
            # Confirm this exact child stopped before resuming it.
            until(lambda: os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)[0] == process.pid)
            process.send_signal(signal.SIGCONT)
            until(lambda: termios.tcgetattr(slave) != previous)
            process.send_signal(signal.SIGTERM)
        elif mode == "exception":
            os.write(master, b"e")
        else:
            process.send_signal(signal.SIGTERM if mode == "term" else signal.SIGHUP)
        until(lambda: process.poll() is not None)
        assert termios.tcgetattr(slave) == previous
        assert b"\x1b[?1049l" in output and b"\x1b[?25h" in output
        assert process.returncode == (1 if mode == "exception" else 129 if mode == "hup" else 143)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGCONT)
            process.terminate()
            process.wait(timeout=3)
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY integration")
def test_resume_emits_redraw_without_a_navigation_key(monkeypatch):
    import io
    import pty
    from dmux.terminal import RESUME, keyboard
    master, slave = pty.openpty()
    calls = []
    try:
        with os.fdopen(os.dup(slave)) as stream:
            monkeypatch.setattr(sys, "stdin", stream)
            monkeypatch.setattr(sys, "stdout", io.StringIO())
            monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
            with keyboard() as read:
                signal.getsignal(signal.SIGTSTP)(signal.SIGTSTP, None)
                assert calls == [(os.getpid(), signal.SIGSTOP)]
                assert read() == [RESUME]
                assert read() == []
                assert not RESUME.isprintable()
    finally:
        os.close(master)
        os.close(slave)


def test_terminal_settings_ignores_only_darwin_pending_input():
    from terminal_helpers import terminal_settings

    # Actual local flags observed on the macOS CI runner.
    previous = [11010, 3, 19200, 1483, 9600, 9600, [b"\x03", b"\x04"]]
    pending = [*previous[:3], 536872395, *previous[4:]]
    options = {"platform": "darwin", "pendin": 0x20000000}
    assert terminal_settings(pending, **options) == previous
    assert pending[3] == 536872395  # Comparing does not mutate either snapshot.
    assert terminal_settings(pending, platform="linux", pendin=0x20000000) != previous

    # Every other setting still matters, including canonical mode and echo.
    for index, mask in ((0, 1), (1, 1), (2, 1), (3, 0x100), (3, 0x8), (4, 1), (5, 1)):
        changed = list(pending)
        changed[index] ^= mask
        assert terminal_settings(changed, **options) != previous
    changed = [*pending[:6], [b"\x05", b"\x04"]]
    assert terminal_settings(changed, **options) != previous
