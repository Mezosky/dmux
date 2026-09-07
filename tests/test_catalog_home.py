import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console

from dmux.catalog import CatalogError, ProjectCatalog, user_directory
from dmux.cli import main
from dmux.home import Home


class NoHost:
    def processes(self, adapter):
        return []

    def gpu(self):
        return {"devices": [], "error": None}


def project(root, *, tag="run-1", current=2, total=4):
    root.mkdir()
    plan_dir = root / "monitor"
    plan_dir.mkdir()
    output = root / "outputs"
    output.mkdir()
    (output / "progress.json").write_text(json.dumps({"current": current, "total": total}))
    (plan_dir / "plan.json").write_text(json.dumps({"project_root": str(root), "disk_warning_gib": 0,
        "tasks": [{"experiment": tag, "stage": "train", "directory": "outputs",
                   "progress": {"type": "json", "path": "progress.json"}}]}))
    return root


def test_empty_catalog_and_bare_dmux_are_read_only(tmp_path, monkeypatch, capsys):
    catalog = ProjectCatalog()
    assert catalog.read() == [] and not catalog.path.parent.exists()
    monkeypatch.chdir(tmp_path)
    main([])
    assert "No registered projects" in capsys.readouterr().out
    assert not catalog.path.parent.exists() and not user_directory("state").exists()


def test_registration_is_idempotent_rename_preserves_id_and_results(tmp_path):
    root = project(tmp_path / "vision")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    catalog = ProjectCatalog()
    first = catalog.add(root, results_dir=root / "external-results")
    again = catalog.add(root)
    renamed = catalog.add(root, name="vision-lab")
    assert first["id"] == again["id"] == renamed["id"]
    assert renamed["results_dir"] == str(root / "external-results")
    assert len(catalog.read()) == 1
    assert catalog.path.stat().st_mode & 0o777 == 0o600
    removed = catalog.remove("vision-lab")
    assert removed["id"] == first["id"] and catalog.read() == []
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_name_collision_and_missing_registration_do_not_overwrite(tmp_path):
    catalog = ProjectCatalog()
    catalog.add(project(tmp_path / "first"), name="lab")
    before = catalog.path.read_bytes()
    with pytest.raises(CatalogError, match="already registered"):
        catalog.add(project(tmp_path / "second"), name="lab")
    with pytest.raises(CatalogError, match="exact"):
        catalog.remove("la")
    assert catalog.path.read_bytes() == before


@pytest.mark.parametrize("content", ["broken", '{"version": 5}', '{"version": 1, "projects": [3]}'])
def test_corrupt_catalog_fails_closed(tmp_path, content):
    catalog = ProjectCatalog()
    catalog.path.parent.mkdir(parents=True)
    catalog.path.write_text(content)
    with pytest.raises(CatalogError):
        catalog.add(project(tmp_path / "vision"))
    assert catalog.path.read_text() == content


def test_global_json_reports_invalid_catalog_with_error_exit(capsys):
    catalog = ProjectCatalog()
    catalog.path.parent.mkdir(parents=True)
    catalog.path.write_text("broken")
    with pytest.raises(SystemExit) as error:
        main(["home", "--json", "--no-gpu"])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["warning"]


def test_settings_paths_honor_xdg_and_ignore_relative_values(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "custom"))
    assert user_directory("config") == tmp_path / "custom" / "dmux"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    assert user_directory("config") == Path.home() / ".config" / "dmux"


def test_concurrent_adds_do_not_lose_registrations(tmp_path):
    roots = [project(tmp_path / f"project-{i}") for i in range(4)]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    children = [subprocess.Popen([sys.executable, "-m", "dmux", "add", str(root)],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for root in roots]
    for child in children:
        stdout, stderr = child.communicate(timeout=8)
        assert child.returncode == 0, (stdout, stderr)
    assert len(ProjectCatalog().read()) == 4


def test_registry_symlink_is_not_overwritten_and_pipes_do_not_block(tmp_path):
    catalog = ProjectCatalog()
    catalog.path.parent.mkdir(parents=True)
    original = tmp_path / "original.json"
    original.write_text('{"version": 1, "projects": []}')
    catalog.path.symlink_to(original)
    with pytest.raises(CatalogError, match="symlink"):
        catalog.add(project(tmp_path / "vision"))
    assert json.loads(original.read_text())["projects"] == []
    catalog.path.unlink()
    os.mkfifo(catalog.path)
    with pytest.raises(CatalogError, match="regular"):
        catalog.read()


def test_home_is_global_and_never_combines_project_denominators(tmp_path, monkeypatch):
    catalog = ProjectCatalog()
    first = project(tmp_path / "vision", total=10)
    second = project(tmp_path / "language", current=3, total=100)
    catalog.add(first)
    catalog.add(second)
    monkeypatch.chdir(tmp_path)
    home = Home(catalog, sampler=NoHost())
    home.refresh(force=True)
    assert len(home.rows()) == 2
    stream = io.StringIO()
    Console(file=stream, width=100, color_system=None).print(home.render(height=24))
    rendered = stream.getvalue()
    assert "vision" in rendered and "language" in rendered
    assert "› language" in rendered
    assert "%" not in rendered and "5 / 110" not in rendered
    home.query = "vision"
    assert home.current()["project"]["name"] == "vision"
    home.query = ""
    home.move(1)
    assert home.current()["project"]["name"] == "vision"
    home.filter = "attention"
    assert len(home.visible()) == 2  # Partial outputs without live processes.


def test_missing_or_broken_project_does_not_hide_other_projects(tmp_path):
    catalog = ProjectCatalog()
    first = project(tmp_path / "available")
    missing = project(tmp_path / "missing")
    catalog.add(first)
    catalog.add(missing)
    (missing / "monitor" / "plan.json").unlink()
    home = Home(catalog, sampler=NoHost())
    home.refresh(force=True)
    assert len(home.rows()) == 2 and any(r["state"] == "unavailable" for r in home.rows())
    assert len(catalog.read()) == 2
    os.mkfifo(missing / "monitor" / "plan.json")
    home.refresh(force=True)
    assert len(home.rows()) == 2


def test_home_refresh_budget_and_completed_backoff(tmp_path, monkeypatch):
    catalog = ProjectCatalog()
    for i in range(6):
        catalog.add(project(tmp_path / f"project-{i}", current=4, total=4))
    now = [0.0]
    home = Home(catalog, sampler=NoHost(), clock=lambda: now[0])
    home.refresh()
    assert len(home.snapshots) == 4
    home.refresh()
    assert len(home.snapshots) == 6
    calls = []
    for mon in home.monitors.values():
        original = mon.snapshot
        monkeypatch.setattr(mon, "snapshot", lambda original=original: calls.append(1) or original())
    now[0] = 5
    home.refresh()
    assert calls == []
    now[0] = 31
    home.refresh()
    assert len(calls) == 4


def test_init_registration_is_explicit_and_dry_run_never_registers(tmp_path, capsys):
    root = tmp_path / "vision"
    root.mkdir()
    main(["init", "--project-root", str(root), "--register", "--dry-run"])
    assert ProjectCatalog().read() == [] and not (root / "monitor").exists()
    main(["init", "--project-root", str(root), "--register", "--yes"])
    assert len(ProjectCatalog().read()) == 1
    main(["projects", "list", "--json"])
    assert "Registered vision" in capsys.readouterr().out


def test_same_stage_identity_is_rejected_but_new_run_tags_are_distinct(tmp_path):
    from dmux.adapters.filesystem import FilesystemAdapter

    task = {"experiment": "run-1", "stage": "train", "directory": "outputs/run-1"}
    with pytest.raises(ValueError, match="repeats"):
        FilesystemAdapter().configure({"tasks": [task, task]})
    FilesystemAdapter().configure({"tasks": [task, {**task, "experiment": "run-2", "directory": "outputs/run-2"}]})


def test_host_process_enumeration_is_shared(monkeypatch):
    from dmux.system import HostSampler
    from dmux.adapters.filesystem import FilesystemAdapter

    calls = []
    monkeypatch.setattr("psutil.process_iter", lambda *args: calls.append(1) or [])
    sampler = HostSampler()
    for _ in range(5):
        assert sampler.processes(FilesystemAdapter()) == []
    assert calls == [1]


def test_init_can_opt_into_metric_fields(tmp_path):
    main(["init", "--project-root", str(tmp_path), "--format", "json",
          "--metric-field", "training.loss", "--metric-field", "validation.accuracy", "--yes"])
    definition = json.loads((tmp_path / "monitor" / "plan.json").read_text())
    assert [m["field"] for m in definition["tasks"][0]["metrics"]] == ["training.loss", "validation.accuracy"]
    assert ProjectCatalog().read() == []


def test_global_detail_roundtrip_and_recent_tab_keep_workers_running(tmp_path):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time
    from dmux.demo import launch_workers, plan

    root = tmp_path / "live-project"
    directory = root / "monitor"
    directory.mkdir(parents=True)
    definition = plan(100, directory)
    definition["tmux"] = {"socket": str(tmp_path / "nonexistent-private-socket")}
    for task in definition["tasks"]:
        task["process"] = {"script": "demo_worker.py", "output_flag": "--out"}
    (directory / "plan.json").write_text(json.dumps(definition))
    ProjectCatalog().add(root)
    workers = launch_workers(root, root / "runs", 100, .2)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    previous = termios.tcgetattr(slave)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), TERM="xterm-256color")
    cli = subprocess.Popen([sys.executable, "-m", "dmux", "home", "--no-gpu"], cwd=tmp_path,
                           env=env, stdin=slave, stdout=slave, stderr=slave)
    def until(token):
        data = b""
        deadline = time.monotonic() + 8
        while token not in data and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                data += os.read(master, 65536)
        assert token in data, data.decode(errors="replace")
        return data
    try:
        until(b"ALL EXPERIMENTS")
        os.write(master, b"/tiny_llm\r\r")
        until(b"best/window")
        os.write(master, b"q")
        until(b"ALL EXPERIMENTS")
        os.write(master, b"x")
        until(b"Recent tab closed")
        os.write(master, b"t")
        until(b"TMUX")
        os.write(master, b"q")
        until(b"ALL EXPERIMENTS")
        os.write(master, b"q")
        deadline = time.monotonic() + 3
        while cli.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                os.read(master, 65536)
        assert cli.wait(timeout=2) == 0
        assert termios.tcgetattr(slave) == previous
        assert sum(worker.poll() is None for worker in workers) == 3
        assert len(ProjectCatalog().read()) == 1
    finally:
        if cli.poll() is None:
            cli.terminate()
            cli.wait(timeout=3)
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=3)
        os.close(master)
        os.close(slave)
