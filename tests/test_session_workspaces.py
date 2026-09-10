import io
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from rich.console import Console

from dmux.adapters.filesystem import FilesystemAdapter
from dmux.monitor import Monitor
from dmux.session_browser import SessionBrowser
from dmux.sessions import SessionError, SessionManager
from dmux.tmux import TmuxNavigator
from test_monitor_tmux import private_tmux


def test_demo_creates_one_workspace_per_experiment_with_independent_outputs(private_tmux, tmp_path):
    socket, env, tmux = private_tmux
    project = tmp_path / "project with spaces"
    results = tmp_path / "external outputs"
    env = dict(env, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    baseline = tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}")
    subprocess.run([sys.executable, "-m", "dmux", "demo", "--project-root", str(project),
                    "--results-dir", str(results), "--tmux", "--tmux-socket", str(socket),
                    "--steps", "2", "--delay", "0"],
                   env=env, capture_output=True, text=True, check=True, timeout=10)
    monitor = Monitor(project / "monitor", project_root=project, adapter=FilesystemAdapter(),
                      gpu=lambda: {"devices": [], "error": None})
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        snapshot = monitor.snapshot()
        if snapshot["state"] == "complete":
            break
        time.sleep(.05)
    assert snapshot["state"] == "complete", snapshot
    assert snapshot["tmux_config"]["socket"] == str(socket)
    nav = TmuxNavigator(socket=socket, env=env, links=snapshot["tmux_config"]["links"])
    browser = SessionBrowser(nav, snapshot["tasks"])
    assert len(browser.rows) == 5  # sentinel plus the four demo sessions
    associations = browser.data["associations"]
    for tag, link in associations.items():
        sid = link["pane"]["session_id"]
        windows = tmux("list-windows", "-t", sid, "-F", "#{window_name}").splitlines()
        assert set(windows) == {"chat", "experiment"}
        assert Path(tmux("show-option", "-v", "-t", sid, "@dmux-results-dir").strip()) == results / tag
    assert baseline.strip() in tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}")
    assert not (project / "runs").exists()
    # The browser discovers project metadata without needing a queue plan.
    browser.key("/")
    for char in "tiny_clip":
        browser.key(char)
    browser.key("\r")
    assert len(browser.rows) == 1
    browser.key("\t")
    assert len(browser.rows) == 2


def test_remove_is_explicit_cancelable_and_revalidates_contents(private_tmux, tmp_path):
    socket, env, tmux = private_tmux
    nav = TmuxNavigator(socket=socket, env=env)
    manager = SessionManager(nav)
    sid = manager.create("disposable", project_root=tmp_path, results_dir="results")
    selected = next(p for p in nav.snapshot([])["panes"] if p["session_id"] == sid)
    request = manager.prepare_removal(selected)
    with pytest.raises(SessionError, match="exact session name"):
        manager.remove(request, confirmation="yes")
    # New windows invalidate the pending confirmation instead of widening it.
    manager.add_window(sid, name="chat2", project_root=tmp_path, results_dir=tmp_path,
                       command=["/bin/sh", "-i"])
    with pytest.raises(SessionError, match="contents changed"):
        manager.remove(request, confirmation="disposable")
    browser = SessionBrowser(nav)
    browser.index = next(i for i, pane in enumerate(browser.rows) if pane["session_id"] == sid)
    browser.key("d")
    output = io.StringIO()
    Console(file=output, width=80, height=24, color_system=None).print(browser.render())
    assert "terminating jobs and AI chats" in output.getvalue()
    browser.key("\x1b")
    assert browser.removal is None and "disposable" in tmux("list-sessions", "-F", "#{session_name}")
    browser.key("d")
    for char in "disposable":
        browser.key(char)
    browser.key("\r")
    assert "Removed session disposable" in browser.notice
    assert tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["train"]


def test_new_workspace_executes_literal_argv_without_shell_or_tmux_command_injection(private_tmux, tmp_path):
    socket, env, tmux = private_tmux
    output = tmp_path / "argv.json"
    script = tmp_path / "record.py"
    script.write_text("import json, pathlib, sys, time\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\ntime.sleep(90)\n")
    special = [";", "kill-server", "$(touch SHOULD_NOT_EXIST)", "`echo bad`", "a b"]
    nav = TmuxNavigator(socket=socket, env=env)
    manager = SessionManager(nav)
    sid = manager.create("literal-argv", project_root=tmp_path,
        command=[sys.executable, str(script), str(output), *special])
    deadline = time.monotonic() + 5
    while not output.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert json.loads(output.read_text()) == special
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()
    assert "train" in tmux("list-sessions", "-F", "#{session_name}")
    selected = next(p for p in nav.snapshot([])["panes"] if p["session_id"] == sid)
    manager.remove(manager.prepare_removal(selected), confirmation="literal-argv")


def test_browser_refuses_to_remove_its_own_session(private_tmux):
    socket, env, tmux = private_tmux
    pane = TmuxNavigator(socket=socket, env=env).snapshot([])["panes"][0]
    nav = TmuxNavigator(socket=socket, env={**env, "TMUX": f"{socket},99,0", "TMUX_PANE": pane["pane_id"]})
    with pytest.raises(SessionError, match="outside this session"):
        SessionManager(nav).prepare_removal(pane)


@pytest.mark.parametrize('failure', [False, True])
def test_live_demo_exit_removes_only_its_sessions(private_tmux, tmp_path, monkeypatch, failure):
    import os
    import psutil
    from dmux.demo import main as demo
    socket, env, tmux = private_tmux
    for key in ('TMUX', 'TMUX_PANE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    baseline = tmux('list-panes', '-a', '-F', '#{session_id}:#{pane_id}:#{pane_pid}')
    owned = []
    def watch(args):
        nav = TmuxNavigator(socket=socket, env=env)
        for pane in nav.snapshot([])['panes']:
            if pane['session'].startswith('cleanup-'):
                owned.append(psutil.Process(pane['pane_pid']))
        assert len(owned) == 8  # Four chat windows and four demo workers.
        if failure:
            raise RuntimeError('owned test dashboard failure')
    monkeypatch.setattr('dmux.cli.main', watch)
    args = ['--live', '--tmux', '--tmux-socket', str(socket), '--session-prefix', 'cleanup',
            '--project-root', str(tmp_path / 'tour'), '--steps', '100', '--delay', '1']
    if failure:
        with pytest.raises(RuntimeError, match='dashboard failure'):
            demo(args)
    else:
        demo(args)
    assert tmux('list-panes', '-a', '-F', '#{session_id}:#{pane_id}:#{pane_pid}') == baseline
    assert all(not process.is_running() or process.status() == psutil.STATUS_ZOMBIE for process in owned)
    assert (tmp_path / 'tour' / 'monitor' / 'plan.json').exists()


def test_demo_cleanup_refuses_changed_session_generation(tmp_path, capsys):
    from types import SimpleNamespace
    from dmux.demo_lifecycle import DemoLifetime
    from dmux.sessions import Removal
    original = Removal('$2', 'demo', ('123\t456', ()), ())
    replacement = Removal('$2', 'demo', ('999\t456', ()), ())
    manager = SimpleNamespace(
        navigator=SimpleNamespace(snapshot=lambda tasks: {'panes': [{'session_id': '$2'}]}),
        prepare_removal=lambda selected: replacement,
        remove=lambda *a, **kw: pytest.fail('removed replacement session'))
    with DemoLifetime() as lifetime:
        lifetime.sessions.append((manager, original))
    assert 'identity changed' in capsys.readouterr().err
