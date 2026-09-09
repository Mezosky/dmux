"""The live tour uses owned subprocesses and never the user's tmux server."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import psutil
import pytest

from dmux.demo import EXPERIMENTS, main, worker_command
from dmux.monitor import Monitor


def test_live_defaults_are_paced_and_do_not_open_a_nonterminal(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("dmux.demo.tempfile.mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr("dmux.demo.launch_workers", lambda *args: calls.append(args) or [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main(["--live"])
    assert calls == [(tmp_path, tmp_path / "runs", 300, 1.0)]
    assert "leaving workers running" in capsys.readouterr().out
    snapshot = Monitor(tmp_path / "monitor", processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    assert len(snapshot["experiments"]) == 4


def test_live_opens_dashboard_when_interactive(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("dmux.demo.launch_workers", lambda *args: [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("dmux.cli.main", lambda args: calls.append(args))
    main(["--live", "--project-root", str(tmp_path)])
    assert calls and calls[0][0] == "watch" and str(tmp_path) in calls[0]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY integration")
def test_live_quit_restores_terminal_and_leaves_demo_workers_running(tmp_path):
    root = tmp_path / "live tour with spaces"
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    previous = termios.tcgetattr(slave)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), TERM="xterm-256color")
    # Exact commands identify only the subprocesses owned by this fixture.
    owned_commands = [worker_command(name, root / "runs", 100, .2) for name in EXPERIMENTS]

    def owned():
        found = []
        for process in psutil.process_iter(["cmdline"]):
            if process.info["cmdline"] in owned_commands:
                found.append(process)
        return found

    cli = subprocess.Popen([sys.executable, "-m", "dmux", "demo", "--live", "--project-root", str(root),
                            "--steps", "100", "--delay", ".2"],
                           cwd=tmp_path, env=env, stdin=slave, stdout=slave, stderr=slave)
    data = b""
    try:
        deadline = time.monotonic() + 8
        while b"Stage progress:" not in data and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                data += os.read(master, 65536)
        assert b"Stage progress:" in data, data.decode(errors="replace")
        os.write(master, b"q")
        while cli.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                data += os.read(master, 65536)
        assert cli.wait(timeout=2) == 0
        assert termios.tcgetattr(slave) == previous
        snapshot = Monitor(root / "monitor", gpu=lambda: {"devices": []}).snapshot()
        assert snapshot["state"] == "running"
        assert sum(bool(task["pid"]) for task in snapshot["tasks"]) == 3
        assert all(task["outputs"] and task["metadata"]["demo"] for task in snapshot["tasks"])
        assert len(owned()) == 3
    finally:
        if cli.poll() is None:
            cli.terminate()
            cli.wait(timeout=3)
        # Cleanup is limited to this test's literal worker argv, not all dmux jobs.
        jobs = owned()
        for job in jobs:
            try:
                job.terminate()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(jobs, timeout=3)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("args", [["--steps", "0"], ["--delay", "nan"], ["--delay", "-1"], ["--live", "--quick"]])
def test_demo_rejects_invalid_options_without_writing(tmp_path, args):
    with pytest.raises(SystemExit) as error:
        main(["--project-root", str(tmp_path), *args])
    assert error.value.code == 2 and list(tmp_path.iterdir()) == []
