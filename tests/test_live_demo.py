"""The live tour uses owned subprocesses and never the user's tmux server."""
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
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
    monkeypatch.setattr("dmux.demo.launch_workers", lambda *args, **kwargs: calls.append(args) or [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main(["--live"])
    assert calls == [(tmp_path, tmp_path / "runs", 300, 1.0)]
    assert "Closing this demo stops its workers" in capsys.readouterr().out
    snapshot = Monitor(tmp_path / "monitor", processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    assert len(snapshot["experiments"]) == 4


def test_live_opens_dashboard_when_interactive(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("dmux.demo.launch_workers", lambda *args, **kwargs: [])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("dmux.cli.main", lambda args: calls.append(args))
    main(["--live", "--project-root", str(tmp_path)])
    assert calls and calls[0][0] == "watch" and str(tmp_path) in calls[0]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY integration")
@pytest.mark.parametrize("exit_mode", ["quit", "interrupt", "term", "keep"])
def test_live_exit_cleans_only_owned_workers_and_restores_terminal(tmp_path, exit_mode):
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
                            "--steps", "100", "--delay", ".2", *(["--keep-running"] if exit_mode == "keep" else [])],
                           cwd=tmp_path, env=env, stdin=slave, stdout=slave, stderr=slave)
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    data = b""
    try:
        deadline = time.monotonic() + 8
        while b"Stage progress:" not in data and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                data += os.read(master, 65536)
        assert b"Stage progress:" in data, data.decode(errors="replace")
        if exit_mode in ("term", "interrupt"):
            # This PTY has no controlling foreground group; deliver the signal
            # that a real terminal generates for Ctrl-C directly to our child.
            cli.send_signal(signal.SIGTERM if exit_mode == "term" else signal.SIGINT)
        else:
            os.write(master, b"q")
        while cli.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                data += os.read(master, 65536)
        assert cli.wait(timeout=2) == (143 if exit_mode == "term" else 130 if exit_mode == "interrupt" else 0)
        assert sentinel.poll() is None
        assert termios.tcgetattr(slave) == previous
        snapshot = Monitor(root / "monitor", gpu=lambda: {"devices": []}).snapshot()
        assert snapshot["state"] == ("running" if exit_mode == "keep" else "interrupted")
        assert sum(bool(task["pid"]) for task in snapshot["tasks"]) == (3 if exit_mode == "keep" else 0)
        assert all(task["outputs"] and task["metadata"]["demo"] for task in snapshot["tasks"])
        assert len(owned()) == (3 if exit_mode == "keep" else 0)
        assert (root / "monitor" / "plan.json").exists()
        assert any((root / "runs").rglob("*.json"))
    finally:
        if cli.poll() is None:
            cli.terminate()
            finish = time.monotonic() + 5
            while cli.poll() is None and time.monotonic() < finish:
                if select.select([master], [], [], .05)[0]:
                    os.read(master, 65536)
            cli.wait(timeout=1)
        sentinel.terminate()
        sentinel.wait(timeout=3)
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


@pytest.mark.parametrize('failure', ['launch', 'registration', 'watch'])
def test_live_errors_cleanup_started_workers_and_keep_plan(tmp_path, monkeypatch, failure):
    original = subprocess.Popen
    started = []
    def launch(*args, **kwargs):
        if failure == 'launch' and started:
            raise OSError('owned test launch failure')
        process = original(*args, **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr('dmux.demo.subprocess.Popen', launch)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    def fail(*args, **kwargs):
        raise OSError('owned test registration failure')
    def watch(*args, **kwargs):
        raise RuntimeError('owned test dashboard failure')
    monkeypatch.setattr('dmux.catalog.ProjectCatalog.add', fail)
    monkeypatch.setattr('dmux.cli.main', watch)
    args = ['--live', '--project-root', str(tmp_path), '--steps', '100', '--delay', '1']
    if failure == 'registration':
        args.append('--register')
    try:
        with pytest.raises(RuntimeError if failure == 'watch' else SystemExit):
            main(args)
        assert started and all(process.poll() is not None for process in started)
        assert (tmp_path / 'monitor' / 'plan.json').exists()
    finally:
        for process in started:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)


@pytest.mark.parametrize('reused', [False, True])
def test_demo_cleanup_allows_owned_exec_but_refuses_pid_reuse(monkeypatch, capsys, reused):
    from types import SimpleNamespace
    from dmux.demo_lifecycle import DemoLifetime
    from dmux.process_actions import ProcessTarget
    terminated = []
    process = SimpleNamespace(pid=42, create_time=lambda: 2.0 if reused else 1.0,
                              cmdline=lambda: ['owned-demo-shell'],
                              terminate=lambda: terminated.append(42))
    monkeypatch.setattr(psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(psutil, 'wait_procs', lambda processes, timeout: (processes, []))
    monkeypatch.setattr('dmux.process_actions._tree',
                        lambda roots: (ProcessTarget(42, 1.0, ('owned-demo-launcher',)),))
    lifetime = DemoLifetime()
    lifetime.workers.append(SimpleNamespace(pid=42, poll=lambda: None, wait=lambda timeout: 0))
    lifetime.close()
    assert terminated == ([] if reused else [42])
    assert ('identity changed' in capsys.readouterr().err) is reused
