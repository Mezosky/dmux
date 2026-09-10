"""Generic integrity, liveness, progress, and terminal regression tests."""
import io
import json
from pathlib import Path
import sys

import pytest

from dmux import FilesystemAdapter, JsonCache, Monitor, tail_log
from dmux.adapters.filesystem import RecordTracker
from dmux.ui import render_dashboard


ROOT = Path(__file__).resolve().parents[1]
NO_GPU = lambda: {"devices": [], "error": None}


def row(sample="sample-1", split="train", transform="original", repeat=0, status="ok", record_id=None):
    return {
        "record_id": record_id or f"{sample}-{split}-{transform}-{repeat}",
        "sample": sample, "split": split, "transform": transform,
        "repeat": repeat, "status": status,
    }


def record_config():
    return {
        "type": "jsonl", "path": "metrics.jsonl",
        "identity": ["record_id"],
        "semantic_identity": ["sample", "split", "transform", "repeat"],
        "status_field": "status", "valid_statuses": ["ok"],
        "excluded_statuses": ["skipped"],
        "group_field": "split", "group_label": "Dataset split",
        "expected_by_group": {"train": 2, "validation": 2},
        "allowed_values": {
            "sample": ["sample-1", "sample-2"],
            "split": ["train", "validation"],
            "transform": ["original", "flipped"], "repeat": [0],
        },
    }


def write_rows(path, rows, mode="w"):
    with path.open(mode) as handle:
        for record in rows:
            handle.write(json.dumps(record) + "\n")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_incremental_counts_ignore_and_later_finish_partial_write(tmp_path):
    path = tmp_path / "metrics.jsonl"
    second = json.dumps(row("sample-2")).encode()
    path.write_bytes(json.dumps(row()).encode() + b"\n" + second[:20])
    tracker = RecordTracker(path, record_config())
    tracker.update()
    assert tracker.count == 1 and tracker.reader.pending
    offset = tracker.reader.offset
    tracker.update()
    assert tracker.reader.offset == offset and tracker.count == 1
    with path.open("ab") as handle:
        handle.write(second[20:] + b"\n")
    tracker.update()
    assert tracker.count == 2 and not tracker.reader.pending


def test_duplicate_semantic_records_and_malformed_rows_do_not_inflate_counts(tmp_path):
    path = tmp_path / "metrics.jsonl"
    write_rows(path, [row(), row(), row(record_id="different-id-same-record")])
    with path.open("a") as handle:
        handle.write("broken json\n")
    tracker = RecordTracker(path, record_config())
    tracker.update()
    progress = tracker.snapshot(now=10, expected=4)
    assert tracker.count == 1 and progress["duplicates"] == 2 and progress["malformed"] == 1
    assert FilesystemAdapter().state({"expected": 4}, progress, alive=True) == "invalid"


def test_truncation_and_replacement_reset_counts(tmp_path):
    path = tmp_path / "metrics.jsonl"
    write_rows(path, [row(), row("sample-2")])
    tracker = RecordTracker(path, record_config())
    tracker.update()
    write_rows(path, [row()])
    tracker.update()
    assert tracker.count == 1
    replacement = tmp_path / "replacement"
    write_rows(replacement, [row("sample-2")])
    replacement.replace(path)
    tracker.update()
    assert tracker.seen == {(row("sample-2")["record_id"],)}


def test_groups_use_configured_totals_and_track_excluded_records(tmp_path):
    path = tmp_path / "metrics.jsonl"
    config = record_config()
    config["expected_by_group"] = {"train": 2, "validation": 4}
    write_rows(path, [row(split="validation", status="skipped")])
    tracker = RecordTracker(path, config)
    tracker.update()
    summary = tracker.snapshot(now=10, expected=6)
    groups = {group["label"]: group for group in summary["breakdown"]}
    assert groups["train"]["expected"] == 2
    assert groups["validation"]["expected"] == 4
    assert summary["excluded"] == 1 and summary["valid"] == 0
    assert groups["validation"]["excluded"] == 1


def test_unexpected_records_are_flagged_without_inflating_saved_counts(tmp_path):
    path = tmp_path / "metrics.jsonl"
    write_rows(path, [row("unknown-sample"), row(split="unknown-split")])
    tracker = RecordTracker(path, record_config())
    tracker.update()
    summary = tracker.snapshot(now=10, expected=4)
    assert summary["unexpected"] == 2
    assert summary["saved"] == 0 and summary["valid"] == 0


def test_observed_progress_does_not_invent_rates_or_an_eta(tmp_path):
    path = tmp_path / "metrics.jsonl"
    write_rows(path, [row()])
    tracker = RecordTracker(path, record_config())
    tracker.update()
    tracker.snapshot(now=100, expected=4)
    write_rows(path, [row("sample-2")], mode="a")
    tracker.update()
    summary = tracker.snapshot(now=120, expected=4)
    assert summary["saved"] == 2
    assert all(group["rate_per_second"] is None and group["eta_seconds"] is None
               for group in summary["breakdown"])


@pytest.mark.parametrize("field", ["record_id", "sample", "split"])
def test_non_scalar_record_keys_are_malformed_not_a_dashboard_crash(tmp_path, field):
    path = tmp_path / "metrics.jsonl"
    write_rows(path, [{**row(), field: ["not", "a", "scalar"]}, row("sample-2")])
    tracker = RecordTracker(path, record_config())
    tracker.update()
    progress = tracker.snapshot(now=10, expected=4)
    assert progress["malformed"] == 1 and progress["saved"] == 1


def test_json_cache_retains_previous_value_during_rewrite(tmp_path):
    path = tmp_path / "status.json"
    write_json(path, {"active": "train"})
    cache = JsonCache()
    assert cache.read(path)["active"] == "train"
    path.write_text('{"active":')
    assert cache.read(path)["active"] == "train" and cache.warnings
    write_json(path, {"active": "evaluate"})
    assert cache.read(path)["active"] == "evaluate" and not cache.warnings


@pytest.fixture
def experiment_plan(tmp_path):
    plan_dir = tmp_path / "monitor"
    output = tmp_path / "results" / "classifier"
    output.mkdir(parents=True)
    task = {
        "experiment": "classifier", "stage": "train", "directory": str(output),
        "expected": 4, "progress": record_config(),
        "process": {"script": "train.py", "output_flag": "--out"},
    }
    write_json(plan_dir / "plan.json", {
        "name": "Experiment lab", "unit": "records",
        "project_root": str(tmp_path),
        "experiments": [
            {"tag": "classifier", "label": "Image classifier"},
            {"tag": "next-run", "label": "Next experiment", "reason": "Awaiting configuration"},
        ],
        "tasks": [task],
        "queue_process": {"script": "scheduler.py", "output_flag": "--out"},
        "pause_file": "PAUSE",
    })
    write_json(plan_dir / "status.json", {
        "active": {"experiment": "classifier", "stage": "train"}, "completed_tasks": [],
    })
    return plan_dir, output, task


def fake_monitor(path, jobs=()):
    return Monitor(path, processes=lambda: list(jobs), gpu=NO_GPU)


def test_stale_running_status_is_not_a_live_process(experiment_plan):
    plan_dir, output, _ = experiment_plan
    write_rows(output / "metrics.jsonl", [row()])
    snapshot = fake_monitor(plan_dir).snapshot()
    assert snapshot["state"] == "interrupted"
    assert snapshot["active"]["state"] == "interrupted"
    assert snapshot["saved"] == 1 and snapshot["completed_stages"] == 0
    assert snapshot["experiments"][1]["blocked"]


def test_pause_marker_does_not_claim_to_stop_active_child(experiment_plan):
    plan_dir, output, _ = experiment_plan
    (plan_dir / "PAUSE").touch()
    jobs = [{"script": "scheduler.py", "out": str(plan_dir), "pid": 100, "started": 0},
            {"script": "train.py", "out": str(output), "pid": 101, "started": 0}]
    assert fake_monitor(plan_dir, jobs).snapshot()["state"] == "pause requested"
    assert fake_monitor(plan_dir).snapshot()["state"] == "paused"


def test_unrelated_scheduler_process_does_not_make_this_run_live(experiment_plan):
    plan_dir, _, _ = experiment_plan
    jobs = [{"script": "scheduler.py", "out": str(plan_dir / "other"), "pid": 100, "started": 0}]
    assert fake_monitor(plan_dir, jobs).snapshot()["state"] == "interrupted"


@pytest.mark.parametrize("declared,present", [(False, False), (True, False), (True, True)])
def test_scheduler_is_optional_and_only_missing_configured_schedulers_warn(experiment_plan, declared, present):
    plan_dir, output, _ = experiment_plan
    plan = json.loads((plan_dir / "plan.json").read_text())
    if not declared:
        plan.pop("queue_process")
    write_json(plan_dir / "plan.json", plan)
    jobs = [{"script": "train.py", "out": str(output), "pid": 101, "started": 0}]
    if present:
        jobs.append({"script": "scheduler.py", "out": str(plan_dir), "pid": 100, "started": 0})
    snapshot = fake_monitor(plan_dir, jobs).snapshot()
    assert snapshot["state"] == "running"
    assert any("scheduler" in warning for warning in snapshot["warnings"]) == (declared and not present)


def test_unconfigured_logs_are_not_inferred_from_project_naming_conventions(experiment_plan):
    plan_dir, output, _ = experiment_plan
    (plan_dir / "classifier_train.log").write_text("unrelated private log")
    jobs = [{"script": "train.py", "out": str(output), "pid": 101, "started": 0}]
    snapshot = fake_monitor(plan_dir, jobs).snapshot()
    assert snapshot["tasks"][0]["log"] is None
    assert snapshot["recent"] == []


@pytest.mark.parametrize("experiment_key", ["experiment", "run", "model", "group"])
def test_generic_task_names_and_previous_aliases_produce_the_same_snapshot(experiment_plan, experiment_key):
    plan_dir, _, _ = experiment_plan
    plan = json.loads((plan_dir / "plan.json").read_text())
    task = plan["tasks"][0]
    task[experiment_key] = task.pop("experiment")
    if experiment_key != "experiment":
        task["name"] = task.pop("stage")
        plan["roster"] = plan.pop("experiments")
    write_json(plan_dir / "plan.json", plan)
    snapshot = fake_monitor(plan_dir).snapshot()
    assert snapshot["experiments"] == snapshot["models"]
    assert snapshot["plan_dir"] == snapshot["queue"] == str(plan_dir)
    assert snapshot["tasks"][0]["experiment"] == snapshot["tasks"][0]["model"] == "classifier"
    assert snapshot["tasks"][0]["stage"] == snapshot["tasks"][0]["name"] == "train"


@pytest.mark.parametrize("plan_flag,experiment_flag", [
    ("--plan-dir", "--experiment"), ("--queue", "--model"),
])
def test_generic_cli_names_and_previous_aliases(experiment_plan, capsys, plan_flag, experiment_flag):
    from dmux.cli import main
    plan_dir, _, _ = experiment_plan
    main(["json", plan_flag, str(plan_dir), experiment_flag, "classifier", "--no-gpu"])
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["experiments"][0]["tag"] == "classifier"
    assert snapshot["plan_dir"] == str(plan_dir)


def test_public_api_and_builtins_depend_only_on_generic_adapter(tmp_path):
    import pkgutil
    import dmux.adapters
    from dmux.registry import BUILTINS, load_adapter

    assert set(BUILTINS) == {"filesystem"}
    assert {module.name for module in pkgutil.iter_modules(dmux.adapters.__path__)} == {"base", "filesystem"}
    assert isinstance(load_adapter("filesystem"), FilesystemAdapter)
    monitor = fake_monitor(tmp_path)
    assert isinstance(monitor.adapter, FilesystemAdapter)
    assert monitor.adapter.presentation.stages == ("run",)
    assert monitor.adapter.presentation.entity_names == {}


def test_snapshot_preserves_generic_presentation_after_json_roundtrip(experiment_plan):
    from rich.console import Console
    plan_dir, _, _ = experiment_plan
    snapshot = json.loads(json.dumps(fake_monitor(plan_dir).snapshot()))
    stream = io.StringIO()
    Console(file=stream, width=120, color_system=None).print(render_dashboard(snapshot))
    rendered = stream.getvalue()
    assert "EXPERIMENT LAB" in rendered
    assert "Image classifier" in rendered
    assert "records saved" in rendered


def test_actual_records_not_completed_status_determine_progress(experiment_plan):
    plan_dir, output, _ = experiment_plan
    write_rows(output / "metrics.jsonl", [row()])
    write_json(plan_dir / "status.json", {"active": None, "completed_tasks": [
        {"experiment": "classifier", "stage": "train", "returncode": 0},
    ]})
    snapshot = fake_monitor(plan_dir).snapshot()
    assert snapshot["state"] == "failed" and snapshot["tasks"][0]["state"] == "invalid"
    write_rows(output / "metrics.jsonl", [
        row(sample, split=split)
        for sample in ("sample-1", "sample-2") for split in ("train", "validation")
    ])
    snapshot = fake_monitor(plan_dir).snapshot()
    assert snapshot["state"] == "complete" and snapshot["completed_stages"] == 1 and snapshot["saved"] == 4


def test_artifact_stage_requires_a_valid_marker_not_only_a_success_code(tmp_path):
    plan_dir = tmp_path / "monitor"
    output = tmp_path / "results"
    write_json(plan_dir / "plan.json", {"tasks": [{
        "experiment": "export", "stage": "package", "directory": str(output),
        "completion": {"type": "json", "path": "artifact.json", "field": "status", "equals": "complete"},
    }]})
    write_json(plan_dir / "status.json", {"completed_tasks": [
        {"experiment": "export", "stage": "package", "returncode": 0},
    ]})
    monitor = fake_monitor(plan_dir)
    assert monitor.snapshot()["tasks"][0]["state"] == "invalid"
    write_json(output / "artifact.json", {})
    assert monitor.snapshot()["tasks"][0]["state"] == "invalid"
    write_json(output / "artifact.json", {"status": "complete"})
    assert monitor.snapshot()["tasks"][0]["state"] == "complete"
    assert FilesystemAdapter().state({}, None, alive=True) == "running"


def test_missing_plan_returns_waiting_without_creating_files(tmp_path):
    path = tmp_path / "absent"
    snapshot = fake_monitor(path).snapshot()
    assert snapshot["state"] == "waiting" and not path.exists()


def test_tail_log_removes_ansi_and_preserves_arbitrary_json(tmp_path):
    path = tmp_path / "run.log"
    record = {"step": 3, "total": 8, "metric": {"loss": 0.125}}
    path.write_text("\x1b[31merror\x1b[0m\n" + json.dumps(record) + "\n")
    lines = tail_log(path)
    assert lines[0] == "error" and json.loads(lines[1]) == record
    assert all("\x1b" not in line for line in lines)


@pytest.mark.parametrize("width,height,expanded", [(80,24,False),(120,40,False),(160,52,False),(100,24,True)])
def test_responsive_render_uses_only_declared_experiments_and_stages(experiment_plan, width, height, expanded):
    from rich.console import Console
    plan_dir, output, _ = experiment_plan
    write_rows(output / "metrics.jsonl", [row()])
    snapshot = fake_monitor(plan_dir).snapshot()
    stream = io.StringIO()
    Console(file=stream, width=width, height=height, color_system=None).print(
        render_dashboard(snapshot, width=width, height=height, expanded=expanded))
    text = stream.getvalue()
    assert "EXPERIMENT LAB" in text and "records saved" in text and "? help" in text
    assert "Image classifier" in text


@pytest.mark.parametrize("width,height", [(80,24), (90,24), (120,40)])
def test_stage_progress_uses_stage_not_experiment_or_total_denominator(experiment_plan, width, height):
    from rich.console import Console
    plan_dir, output, task = experiment_plan
    write_rows(output / "metrics.jsonl", [row()])
    plan = json.loads((plan_dir / "plan.json").read_text())
    plan["tasks"].append({**task, "stage": "evaluate", "directory": str(output / "evaluate"), "expected": 20})
    write_json(plan_dir / "plan.json", plan)
    stream = io.StringIO()
    Console(file=stream, width=width, height=height, color_system=None).print(
        render_dashboard(fake_monitor(plan_dir).snapshot(), width=width, height=height))
    lines = stream.getvalue().splitlines()
    stage_line = next(line for line in lines if "Stage progress:" in line)
    assert "1 / 4 saved" in stage_line and "25.0%" in stage_line
    assert "1 / 24 records saved" in stream.getvalue()
    assert len(lines) <= height


def test_selected_stage_progress_updates_with_navigation(experiment_plan):
    from rich.console import Console
    plan_dir, output, task = experiment_plan
    write_rows(output / "metrics.jsonl", [row()])
    plan = json.loads((plan_dir / "plan.json").read_text())
    plan["tasks"].append({**task, "stage": "evaluate", "directory": str(output / "evaluate"), "expected": 20})
    write_json(plan_dir / "plan.json", plan)
    stream = io.StringIO()
    Console(file=stream, width=90, color_system=None).print(
        render_dashboard(fake_monitor(plan_dir).snapshot(), width=90, height=24, stage="evaluate"))
    stage_line = next(line for line in stream.getvalue().splitlines() if "Stage progress:" in line)
    assert "0 / 20 saved" in stage_line and "0.0%" in stage_line


@pytest.mark.parametrize("state", ["queued", "running", "complete"])
def test_artifact_stage_does_not_invent_a_percentage(experiment_plan, state):
    from rich.console import Console
    plan_dir, _, _ = experiment_plan
    snapshot = fake_monitor(plan_dir).snapshot()
    snapshot["tasks"].append({
        **snapshot["tasks"][0], "name": "export", "stage": "export", "label": "Export",
        "expected": None, "progress": None, "state": state,
    })
    stream = io.StringIO()
    Console(file=stream, width=90, color_system=None).print(
        render_dashboard(snapshot, width=90, height=24, stage="export"))
    stage_line = next(line for line in stream.getvalue().splitlines() if "Stage progress:" in line)
    assert state in stage_line and "%" not in stage_line
    assert ("completion artifact present" if state == "complete" else "no incremental counter") in stage_line


@pytest.mark.skipif(sys.platform != "linux", reason="PTY smoke check uses Linux terminal controls")
def test_interactive_quit_restores_terminal_and_does_not_edit_project(experiment_plan):
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import termios
    import time
    plan_dir, _, _ = experiment_plan
    originals = {str(path): path.read_bytes() for path in plan_dir.rglob("*") if path.is_file()}
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 140, 0, 0))
    previous = termios.tcgetattr(slave)
    env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=str(ROOT / "src"))
    process = subprocess.Popen([sys.executable, "-m", "dmux", "--plan-dir", str(plan_dir),
        "--interval", "0.25", "--no-gpu", "--color", "always"],
        stdin=slave, stdout=slave, stderr=slave, env=env)
    output = b""
    try:
        deadline = time.monotonic() + 5
        while b"EXPERIMENT LAB" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
        assert b"EXPERIMENT LAB" in output
        os.write(master, b"n]aq")
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
        assert process.wait(timeout=1) == 0
        while select.select([master], [], [], .05)[0]:
            output += os.read(master, 65536)
        assert b"Monitor closed" in output
        assert b"\x1b[?1049h" in output and b"\x1b[?1049l" in output
        assert termios.tcgetattr(slave) == previous
        assert {str(path): path.read_bytes() for path in plan_dir.rglob("*") if path.is_file()} == originals
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        os.close(master)
        os.close(slave)
