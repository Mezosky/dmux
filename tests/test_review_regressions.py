"""Regression coverage for the incremental engineering review fixes."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dmux.monitor import Monitor
from dmux.status import scheduler_records
from dmux.ui import selected_task


@pytest.mark.parametrize("filename,value", [
    ("status.json", []), ("status.json", 1), ("status.json", None),
    ("status.json", {"active": "train"}),
    ("status.json", {"active": {"experiment": []}}),
    ("status.json", {"completed_tasks": {}}),
    ("status.json", {"completed_tasks": [{}]}),
    ("completion.json", {}), ("completion.json", None),
    ("completion.json", [1]), ("completion.json", [{"returncode": False}]),
    ("completion.json", [{"experiment": [], "returncode": 0}]),
])
def test_malformed_scheduler_files_warn_without_crashing(tmp_path, filename, value):
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [{"experiment": "test"}]}))
    (tmp_path / filename).write_text(json.dumps(value))
    monitor = Monitor(tmp_path, processes=lambda: [], gpu=lambda: {"devices": []})
    snapshot = monitor.snapshot()
    assert any(filename in warning for warning in snapshot["warnings"])
    assert snapshot["tasks"][0]["pid"] is None
    (tmp_path / filename).unlink()
    assert not monitor.snapshot()["warnings"]


def test_valid_scheduler_rows_survive_bad_neighbors():
    _, codes, warnings = scheduler_records({}, [None, {"experiment": "test", "returncode": 1}])
    assert codes == {("test", "run"): 1} and len(warnings) == 1


def test_gpu_bad_line_preserves_healthy_devices(monkeypatch):
    from dmux.system import gpu_info
    monkeypatch.setattr("dmux.system.subprocess.run", lambda *a, **kw: SimpleNamespace(
        stdout="good, 12, 1024, 2048\nbad, [N/A], 10, 20\nother, 20, 1024, 4096\nnan, nan, 1, 2"))
    data = gpu_info()
    assert [d["name"] for d in data["devices"]] == ["good", "other"]
    assert "2 malformed" in data["error"]


def test_selection_prefers_attention_and_last_completed():
    tasks = [{"model": "test", "name": name, "state": state, "pid": None}
             for name, state in [("first", "queued"), ("second", "failed")]]
    snapshot = {"tasks": tasks}
    assert selected_task(snapshot, "test") is tasks[1]
    assert selected_task(snapshot, "test", "first") is tasks[0]
    for task in tasks:
        task["state"] = "complete"
    assert selected_task(snapshot, "test") is tasks[1]


def test_demo_plan_create_refuses_existing_and_symlink(tmp_path):
    from dmux.demo import create_plan
    target = tmp_path / "plan.json"
    target.write_text("original")
    with pytest.raises(FileExistsError):
        create_plan(target, {})
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        create_plan(link, {})
    assert target.read_text() == "original"


def test_monitor_imports_no_training_frameworks():
    result = subprocess.run([sys.executable, "-S", "-c", "import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parents[1] / "src")) + ");" + """
import sys
import dmux.cli, dmux.demo, dmux.metrics
for name in ('torch', 'tensorflow', 'jax', 'transformers', 'vllm', 'cupy', 'cuda'):
    assert not any(m == name or m.startswith(name + '.') for m in sys.modules), name
"""], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_missing_psutil_has_install_message():
    result = subprocess.run([sys.executable, "-c", """
import sys
sys.modules['psutil'] = None
from dmux.cli import main
main(['watch', '--no-gpu'])
"""], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "Missing psutil" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("parent", [False, True])
def test_stop_protects_dmux_and_ancestors(parent, monkeypatch):
    import psutil
    from dmux.process_actions import ProcessActionError, _identity, _tree
    process = psutil.Process().parent() if parent else psutil.Process()
    monkeypatch.setattr(psutil.Process, "terminate", lambda self: pytest.fail("sent signal"))
    with pytest.raises(ProcessActionError, match="dmux itself or its parent"):
        _tree([_identity(process)])


def test_init_registration_failure_retains_created_plan(tmp_path, monkeypatch, capsys):
    from dmux.onboarding import main
    def fail(*args, **kwargs):
        raise OSError("registration unavailable")
    monkeypatch.setattr("dmux.catalog.ProjectCatalog.add", fail)
    with pytest.raises(SystemExit) as error:
        main(["--project-root", str(tmp_path), "--yes", "--register"])
    assert error.value.code == 2
    assert list(tmp_path.rglob("plan.json"))
    assert "Plan was created, but registration failed" in capsys.readouterr().err


def test_demo_reports_sessions_if_plan_write_fails(tmp_path, monkeypatch, capsys):
    from dmux.demo import main
    monkeypatch.setattr("dmux.tmux.TmuxNavigator.snapshot", lambda *a: {"panes": []})
    created = []
    def create(*args, **kwargs):
        created.append(f"${len(created) + 1}")
        return created[-1]
    monkeypatch.setattr("dmux.sessions.SessionManager.create", create)
    monkeypatch.setattr("dmux.sessions.SessionManager.add_window", lambda *a, **kw: pytest.fail("launched worker"))
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr("dmux.demo.create_plan", fail)
    with pytest.raises(SystemExit) as error:
        main(["--tmux", "--project-root", str(tmp_path)])
    assert error.value.code == 2
    assert "Created sessions: $1, $2, $3, $4" in capsys.readouterr().err


def test_controller_arrows_help_and_hidden_tabs(tmp_path, monkeypatch):
    from dmux.adapters.filesystem import FilesystemAdapter
    from dmux.controller import DashboardController
    from dmux.terminal import UP, DOWN
    from dmux.ui import render_dashboard
    from rich.console import Console
    import io
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [{"experiment": "one"}, {"experiment": "two"}]}))
    adapter = FilesystemAdapter()
    snapshot = Monitor(tmp_path, adapter=adapter, processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    c = DashboardController(snapshot, adapter, None)
    monkeypatch.setattr("dmux.controller.prepare_stop", lambda *args: pytest.fail("arrow opened stop"))
    c.key(UP)
    assert c.current_model() == "two" and c.pending_stop is None
    c.key(DOWN)
    assert c.current_model() == "one"
    c.key("?")
    c.key("k")
    assert c.help and c.pending_stop is None
    c.key("\x1b")
    c.key("x")
    c.key("x")
    assert c.current_model() is None
    out = io.StringIO()
    Console(file=out, width=100).print(render_dashboard(c.visible_snapshot()))
    assert "Press u" in out.getvalue() and "No experiments are declared" not in out.getvalue()
    c.key("u")
    assert c.current_model() == "one"


def test_controller_stop_uses_same_stage_and_exact_confirmation(tmp_path, monkeypatch):
    from dmux.adapters.filesystem import FilesystemAdapter
    from dmux.controller import DashboardController
    from dmux.terminal import UP
    calls = []
    tasks = [{"model": "test", "name": n, "pid": None, "state": state}
             for n, state in [("queued", "queued"), ("failed", "failed")]]
    c = DashboardController({"models": [{"tag": "test"}], "tasks": tasks}, FilesystemAdapter(), None)
    monkeypatch.setattr("dmux.controller.prepare_stop", lambda snap, run, stage: calls.append(stage) or SimpleNamespace(label="test/failed"))
    monkeypatch.setattr("dmux.controller.stop", lambda request, **kw: calls.append(kw["confirmation"]) or "requested")
    c.key("k")
    assert calls == ["failed"]
    c.key(UP)
    assert c.stop_confirmation == ""
    for key in "test/failed\r":
        c.key(key)
    assert calls == ["failed", "test/failed"]


def test_preview_cache_invalidates_changed_files_and_discovers_new_files(tmp_path, monkeypatch):
    from dmux.experiment_view import PreviewReader, output_previews
    from dmux.connectors import tail_log
    calls = []
    now = [0.0]
    reader = PreviewReader(clock=lambda: now[0])
    monkeypatch.setattr("dmux.experiment_view.tail_log", lambda *a, **kw: calls.append(a[0]) or tail_log(*a, **kw))
    path = tmp_path / "first.log"
    path.write_text("first\n")
    task = {"directory": str(tmp_path), "outputs": ["*.log"]}
    assert output_previews(task, reader)[0][2] == ["first"]
    output_previews(task, reader)
    assert len(calls) == 1
    path.write_text("changed\n")
    assert output_previews(task, reader)[0][2] == ["changed"]
    (tmp_path / "new.log").write_text("new\n")
    assert len(output_previews(task, reader)) == 1
    now[0] = 2
    assert len(output_previews(task, reader)) == 2
    path.unlink()
    assert len(output_previews(task, reader)) == 1


def test_background_refresh_is_single_flight_and_does_not_block_keys():
    from threading import Event
    from dmux.refresh import BackgroundRefresh
    entered, release = Event(), Event()
    def read():
        entered.set()
        release.wait(3)
        return {"value": 1}
    worker = BackgroundRefresh(read)
    try:
        worker.request()
        assert entered.wait(1)
        worker.request()
        assert worker.take() is None
        assert worker.pending
    finally:
        release.set()
    # Queue delivery is deterministic; no timing assertion or busy polling.
    result = worker.results.get(timeout=2)
    worker.results.put(result)
    assert worker.take() == ({"value": 1}, None)
    assert not worker.pending


def test_host_sampler_reuses_cwd_per_scan(monkeypatch):
    from dmux.system import HostSampler
    from dmux.adapters.filesystem import FilesystemAdapter
    adapter = FilesystemAdapter()
    adapter.configure({"tasks": [{"process": {"script": "worker.py"}}]})
    calls = []
    process = SimpleNamespace(info={"pid": 42, "create_time": 1, "cmdline": ["python", "worker.py", "--out", "runs"], "status": "running"},
                              cwd=lambda: calls.append(1) or "/tmp")
    monkeypatch.setattr("psutil.process_iter", lambda *a: [process])
    sampler = HostSampler()
    assert sampler.processes(adapter) == sampler.processes(adapter)
    assert len(calls) == 1


def test_packaged_plan_schema_accepts_examples_and_rejects_bad_fields():
    from dmux.demo import plan
    from dmux.plan_schema import validate_plan_schema
    root = Path(__file__).resolve().parents[1]
    validate_plan_schema(json.loads((root / "examples/plan.json").read_text()))
    validate_plan_schema(plan(2, Path("/tmp/owned-demo/monitor")))
    validate_plan_schema({"tasks": [{"model": "test", "name": "run", "expected": None}]})
    with pytest.raises(ValueError, match="tasks"):
        validate_plan_schema({"tasks": [{"expected": True}]})
    with pytest.raises(ValueError, match="directory"):
        validate_plan_schema({"tasks": [{"progress": {"type": "json", "path": "progress.json"}}]})


def test_doctor_validates_bundled_schema_and_never_fetches_declared_url(tmp_path, monkeypatch):
    from dmux.diagnostics import diagnose
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("doctor launched a tool"))
    (tmp_path / "plan.json").write_text(json.dumps({"$schema": "https://invalid.example/schema", "tasks": []}))
    report = diagnose(tmp_path, processes=lambda: [])
    assert any(check["code"] == "plan" and check["level"] == "ok" for check in report["checks"])
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [{"progress": "bad"}]}))
    report = diagnose(tmp_path, processes=lambda: [])
    assert report["exit_code"] == 2


@pytest.mark.parametrize("plan,state", [(None, "waiting"), ({"tasks": [1]}, "invalid"), ({"tasks": []}, "idle")])
def test_snapshot_schema_version_on_every_state(tmp_path, plan, state):
    if plan is not None:
        (tmp_path / "plan.json").write_text(json.dumps(plan))
    snapshot = Monitor(tmp_path, processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    assert snapshot["schema_version"] == 1 and snapshot["state"] == state
    assert snapshot["experiments"] == snapshot["models"]
    assert snapshot["plan_dir"] == snapshot["queue"]


def test_cli_subcommands_keep_scoped_aliases_and_versions(tmp_path, capsys):
    from dmux import __version__
    from dmux.cli import main
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": []}))
    for args in (["json", "--queue"], ["--json", "--plan-dir"]):
        main([*args, str(tmp_path), "--no-gpu", "--tmux-socket", str(tmp_path / "absent.sock")])
        assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    for args in (["--version"], ["watch", "--version"]):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 0 and __version__ in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(["json", "--once"])
    assert error.value.code == 2


def test_keyboard_reference_matches_binding_table():
    from dmux.bindings import BINDINGS, markdown
    path = Path(__file__).resolve().parents[1] / "docs/KEYS.md"
    assert path.read_text() == markdown()
    for view in ("dashboard", "detail", "home", "sessions"):
        keys = [key for binding in BINDINGS if view in binding.views for key in binding.keys]
        assert len(keys) == len(set(keys)), f"ambiguous {view} binding"
