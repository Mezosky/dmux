import json
import sys

import pytest

from dmux.commands import read_command
from dmux.diagnostics import diagnose
from dmux.monitor import Monitor
from dmux.templates import expand_templates
from dmux_slurm import SlurmAdapter


def template(**extra):
    return {"tasks": [], "task_templates": [{"glob": "sweep/*", "tag_prefix": "sweep",
        "task": {"stage": "train", "progress": {"type": "json", "path": "progress.json"}}, **extra}]}


def test_sweep_expansion_is_bounded_stable_and_read_only(tmp_path):
    for name in ("seed-2", "seed-1"):
        path = tmp_path / "sweep" / name
        path.mkdir(parents=True)
        (path / "progress.json").write_text('{"current": 1, "total": 2}')
    plan = template()
    expanded = expand_templates(plan, tmp_path)
    assert [t["experiment"] for t in expanded["tasks"]] == ["sweep/sweep/seed-1", "sweep/sweep/seed-2"]
    assert plan["tasks"] == [] and "directory" not in plan["task_templates"][0]["task"]
    (tmp_path / "plan.json").write_text(json.dumps({"project_root": str(tmp_path), **plan}))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = Monitor(tmp_path, processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    assert len(snapshot["tasks"]) == 2 and snapshot["saved"] == 2
    assert not diagnose(tmp_path, processes=lambda: [])["counts"].get("error")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_sweeps_do_not_follow_symlinks_or_escape_root(tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    results = tmp_path / "results"
    results.mkdir()
    (results / "sweep").symlink_to(external, target_is_directory=True)
    assert expand_templates(template(), results)["tasks"] == []
    for glob in ("../*", "**/*", "/tmp/*", "a//b"):
        with pytest.raises(ValueError, match="glob"):
            expand_templates(template(glob=glob), results)


def test_sweeps_overflow_fails_instead_of_hiding_runs(tmp_path):
    for i in range(5):
        (tmp_path / "sweep" / str(i)).mkdir(parents=True)
    for limits, reason in (({"max_matches": 2}, "max_matches"), ({"max_scan": 2}, "max_scan")):
        with pytest.raises(ValueError, match=reason):
            expand_templates(template(**limits), tmp_path)


def test_sweep_reexpands_and_checks_empty_template_structure(tmp_path):
    plan = template()
    assert expand_templates(plan, tmp_path)["tasks"] == []
    (tmp_path / "sweep" / "first").mkdir(parents=True)
    assert len(expand_templates(plan, tmp_path)["tasks"]) == 1
    plan["task_templates"][0]["task"]["progress"]["type"] = "invented"
    with pytest.raises(ValueError):
        expand_templates(plan, tmp_path / "not-created")


def test_sweep_named_project_and_duplicate_stage_identity(tmp_path):
    root = tmp_path / "code"
    output = tmp_path / "results" / "sweep" / "first"
    output.mkdir(parents=True)
    plan = template()
    plan["projects"] = {"lab": {"root": str(root), "results_dir": "../results"}}
    plan["task_templates"][0]["task"]["project"] = "lab"
    expanded = expand_templates(plan, tmp_path)
    assert expanded["tasks"][0]["directory"] == str(output)
    assert expanded["tasks"][0]["project"] == "lab"
    plan["task_templates"] *= 2
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    snapshot = Monitor(tmp_path, project_root=tmp_path, processes=lambda: [], gpu=lambda: {}).snapshot()
    assert snapshot["state"] == "invalid" and "repeats an experiment/stage identity" in snapshot["message"]


def test_slurm_batches_explicit_jobs_and_uses_accounting_for_completed(tmp_path, monkeypatch):
    commands = []
    def command(argv):
        commands.append(argv)
        return "12|RUNNING\n" if argv[0] == "squeue" else "13_2|COMPLETED\n"
    monkeypatch.setattr("dmux_slurm.adapter.read_command", command)
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [
        {"experiment": "active", "slurm": {"job_id": "12"}},
        {"experiment": "done", "slurm": {"job_id": "13_2"}}]}))
    mon = Monitor(tmp_path, adapter=SlurmAdapter(), processes=lambda: [], gpu=lambda: {"devices": []})
    snapshot = mon.snapshot()
    assert [t["state"] for t in snapshot["tasks"]] == ["scheduler running", "complete"]
    assert snapshot["state"] == "scheduler running" and all(t["pid"] is None for t in snapshot["tasks"])
    assert [c[0] for c in commands] == ["squeue", "sacct"]
    assert "--jobs=12,13_2" in commands[0] and "--jobs=13_2" in commands[1]
    assert "--duplicates" in commands[1] and "--allocations" in commands[1]
    mon.snapshot()
    assert len(commands) == 2


def test_slurm_job_id_file_and_doctor_never_executes_tools(tmp_path, monkeypatch):
    (tmp_path / "job.id").write_text("41\n")
    (tmp_path / "plan.json").write_text(json.dumps({"project_root": str(tmp_path), "tasks": [
        {"directory": ".", "slurm": {"job_id_file": "job.id"}}]}))
    monkeypatch.setattr("dmux.diagnostics.load_adapter", lambda name: SlurmAdapter())
    monkeypatch.setattr("dmux_slurm.adapter.read_command", lambda *a: pytest.fail("doctor invoked a scheduler command"))
    report = diagnose(tmp_path, adapter_name="slurm", processes=lambda: [])
    assert any("disabled in doctor" in check["message"] for check in report["checks"])
    (tmp_path / "job.id").write_text("41; rm -rf /\n")
    mon = Monitor(tmp_path, adapter=SlurmAdapter(), processes=lambda: [], gpu=lambda: {"devices": []})
    assert mon.snapshot()["tasks"][0]["state"] == "unavailable"


def test_slurm_squeue_missing_job_falls_back_to_sacct(tmp_path, monkeypatch):
    def command(argv):
        if argv[0] == "squeue":
            raise ValueError("Invalid job id specified")
        return "41|FAILED\n"
    monkeypatch.setattr("dmux_slurm.adapter.read_command", command)
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [{"slurm": {"job_id": "41"}}]}))
    mon = Monitor(tmp_path, adapter=SlurmAdapter(), processes=lambda: [], gpu=lambda: {"devices": []})
    assert mon.snapshot()["tasks"][0]["state"] == "failed"


def test_slurm_accounting_failure_keeps_healthy_queue_jobs(tmp_path, monkeypatch):
    def command(argv):
        if argv[0] == "squeue":
            return "41|RUNNING\n"
        raise ValueError("sacct timed out after 2s")
    monkeypatch.setattr("dmux_slurm.adapter.read_command", command)
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": [
        {"experiment": "live", "slurm": {"job_id": "41"}},
        {"experiment": "absent", "slurm": {"job_id": "42"}}]}))
    mon = Monitor(tmp_path, adapter=SlurmAdapter(), processes=lambda: [], gpu=lambda: {"devices": []})
    snapshot = mon.snapshot()
    assert [t["state"] for t in snapshot["tasks"]] == ["scheduler running", "unavailable"]
    assert "timed out" in snapshot["tasks"][1]["scheduler"]["error"]
    assert snapshot["tasks"][0]["scheduler"]["error"] is None


def test_slurm_sweep_and_home_do_not_invent_pid_or_completion(tmp_path, monkeypatch):
    from dmux.catalog import ProjectCatalog
    from dmux.home import Home
    from dmux.system import HostSampler
    for name in ("one", "two"):
        directory = tmp_path / "sweep" / name
        directory.mkdir(parents=True)
        (directory / "job.id").write_text("41" if name == "one" else "42")
        (directory / "progress.json").write_text('{"current": 1, "total": 2}')
    plan = template()
    plan["project_root"] = str(tmp_path)
    plan["task_templates"][0]["task"]["slurm"] = {"job_id_file": "job.id"}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr("dmux_slurm.adapter.read_command", lambda argv:
                        "41|RUNNING\n" if argv[0] == "squeue" else "42|COMPLETED\n")
    monkeypatch.setattr("dmux.catalog.load_adapter", lambda name: SlurmAdapter())
    monkeypatch.setattr("dmux.home.load_adapter", lambda name: SlurmAdapter())
    monkeypatch.setattr(HostSampler, "processes", lambda *a: [])
    monkeypatch.setattr(HostSampler, "gpu", lambda *a: {"devices": []})
    catalog = ProjectCatalog()
    catalog.add(tmp_path, adapter="slurm")
    home = Home(catalog)
    home.refresh(force=True)
    rows = home.rows()
    assert not any(row["pids"] for row in rows)
    assert {row["state"] for row in rows} == {"scheduler running", "interrupted"}
    home.filter = "running"
    assert len(home.visible()) == 1 and home.visible()[0]["state"] == "scheduler running"


def test_slurm_background_discards_observations_for_previous_selection(tmp_path, monkeypatch):
    adapter = SlurmAdapter()
    adapter.configure_observation(background=True)
    old = ("41",)
    adapter.ids = old
    adapter.worker.results.put(((old, {"41": "RUNNING"}, {}), None))
    requested = []
    monkeypatch.setattr(adapter.worker, "request", lambda: requested.append(True))
    adapter.prepare_poll([({"model": "run", "name": "train", "slurm": {"job_id": "42"}}, tmp_path)], now=0)
    assert not adapter.records and requested


@pytest.mark.parametrize("text", ["41|RUNNING\n41|COMPLETED\n", "42|RUNNING\n", "41|BAD+\n", "warning from server"])
def test_ambiguous_or_malformed_scheduler_records_are_rejected(text):
    with pytest.raises(ValueError):
        SlurmAdapter._parse(text, ["41"])


def test_slurm_background_poll_does_not_wait_on_key_thread(tmp_path, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    def command(argv):
        entered.set()
        release.wait(2)
        return "41|RUNNING\n"
    monkeypatch.setattr("dmux_slurm.adapter.read_command", command)
    adapter = SlurmAdapter()
    adapter.configure_observation(background=True)
    task = {"model": "run", "name": "train", "slurm": {"job_id": "41"}}
    try:
        adapter.prepare_poll([(task, tmp_path)], now=0)
        assert entered.wait(1) and adapter.records == {}
    finally:
        release.set()
    result = adapter.worker.results.get(timeout=3)
    adapter.worker.results.put(result)
    adapter.prepare_poll([(task, tmp_path)], now=1)
    assert adapter.records == {"41": "RUNNING"}


@pytest.mark.skipif(sys.platform != "linux", reason="owned probe subprocess regression")
def test_probe_output_and_timeout_are_bounded():
    assert read_command([sys.executable, "-c", "print('ok')"]) == "ok\n"
    with pytest.raises(ValueError, match="exceeded"):
        read_command([sys.executable, "-c", "print('x'*10000)"], max_bytes=64)
    with pytest.raises(ValueError, match="timed out"):
        read_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=.05)
