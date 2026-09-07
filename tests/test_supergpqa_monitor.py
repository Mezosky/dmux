"""Read-only dashboard tests. Synthetic fixture counts are not research evidence."""
import io
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from monitor_supergpqa import JsonCache, Monitor, RowTracker, render_dashboard, task_state, tail_log


def row(uid="q1", arm="ZS", length=0, replicate=0, status="ok", cid=None):
    return {"cell_id": cid or f"{uid}-{arm}-{length}-{replicate}", "target_uuid": uid,
        "arm": arm, "length": length, "replicate": replicate, "status": status}


def manifest(targets=("q1", "q2"), grid=(("ZS", 0, 0), ("ZS", 1000, 0))):
    return {"target_uuids": list(targets), "grid": list(grid), "dry_run": False,
        "expected_cells": len(targets) * len(grid)}


def write_rows(path, rows, mode="w"):
    with path.open(mode) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_incremental_counts_ignore_and_later_finish_partial_write(tmp_path):
    path = tmp_path / "rows.jsonl"
    first = row()
    second = json.dumps(row("q2")).encode()
    path.write_bytes(json.dumps(first).encode() + b"\n" + second[:20])
    tracker = RowTracker(path)
    tracker.update(manifest(), now=0)
    assert tracker.count == 1 and tracker.pending
    offset = tracker.offset
    tracker.update(manifest(), now=1)
    assert tracker.offset == offset and tracker.count == 1
    with path.open("ab") as f:
        f.write(second[20:] + b"\n")
    tracker.update(manifest(), now=2)
    assert tracker.count == 2 and not tracker.pending
    assert tracker.snapshot(manifest())["unique_targets"] == 2


def test_duplicate_semantic_cells_and_malformed_rows_do_not_inflate_counts(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_rows(path, [row(), row(), row(cid="different-id-same-cell")])
    with path.open("a") as f:
        f.write("broken json\n")
    tracker = RowTracker(path)
    tracker.update(manifest())
    assert tracker.count == 1 and tracker.duplicates == 2 and tracker.malformed == 1
    assert task_state({"expected_cells": 4}, tracker.snapshot(manifest()), alive=True) == "invalid"


def test_truncation_and_replacement_reset_counts(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_rows(path, [row(), row("q2")])
    tracker = RowTracker(path)
    tracker.update(manifest())
    write_rows(path, [row()])
    tracker.update(manifest())
    assert tracker.count == 1
    replacement = tmp_path / "replacement"
    write_rows(replacement, [row("q2")])
    replacement.replace(path)
    tracker.update(manifest())
    assert tracker.targets == {"q2"}


def test_windows_use_actual_manifest_arm_multiplicity(tmp_path):
    path = tmp_path / "rows.jsonl"
    m = manifest(grid=(("ZS",0,0), ("ZS",8000,0), ("ID5_late",8000,0)))
    write_rows(path, [row(length=8000, status="invalid_context")])
    tracker = RowTracker(path)
    tracker.update(m)
    summary = tracker.snapshot(m)
    assert [w["expected"] for w in summary["windows"]] == [2,4]
    assert summary["excluded"] == 1 and summary["valid"] == 0
    assert summary["windows"][1]["excluded"] == 1


def test_unknown_question_or_grid_cell_is_flagged(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_rows(path, [row("not-in-panel"), row(length=120000)])
    tracker = RowTracker(path)
    tracker.update(manifest())
    assert tracker.unexpected == 2


def test_eta_requires_live_observations_and_only_current_window(tmp_path):
    path = tmp_path / "rows.jsonl"
    m = manifest(targets=tuple(f"q{i}" for i in range(20)))
    write_rows(path, [row("q0")])
    tracker = RowTracker(path)
    tracker.update(m, now=100)
    assert tracker.rate(0) is None
    write_rows(path, [row(f"q{i}") for i in range(1,6)], mode="a")
    tracker.update(m, now=120)
    assert tracker.rate(0) == pytest.approx(.25)
    windows = tracker.snapshot(m, now=120)["windows"]
    assert windows[0]["eta_seconds"] == pytest.approx(56)
    assert windows[1]["eta_seconds"] is None


def test_json_cache_retains_previous_value_during_rewrite(tmp_path):
    path = tmp_path / "status.json"
    write_json(path, {"active": "train"})
    cache = JsonCache()
    assert cache.read(path)["active"] == "train"
    path.write_text('{"active":')
    assert cache.read(path)["active"] == "train" and cache.warnings
    write_json(path, {"active": "test"})
    assert cache.read(path)["active"] == "test" and not cache.warnings


@pytest.fixture
def queue(tmp_path):
    q = tmp_path / "queue"
    d = q / "e4b/train"
    task = {"model": "e4b", "name": "train", "directory": str(d), "expected_cells": 4,
        "command": ["python", "exp16_supergpqa_wildchat.py", "--out", str(d)]}
    write_json(q / "plan.json", {"tasks": [task], "roster": [
        {"tag": "e4b", "model_id": "test-model", "status": "queued"},
        {"tag": "phi4", "model_id": "test-blocked", "status": "blocked", "audit_status": "needs_validation"}]})
    write_json(d / "run.json", manifest())
    write_json(q / "status.json", {"active": {"model": "e4b", "name": "train"}, "completed_tasks": []})
    return q, d, task


def fake_monitor(path, jobs=()):
    return Monitor(path, processes=lambda: list(jobs), gpu=lambda: {"devices": [], "error": None})


def test_stale_running_status_is_not_a_live_process(queue):
    q, d, _ = queue
    write_rows(d / "rows.jsonl", [row()])
    s = fake_monitor(q).snapshot()
    assert s["state"] == "interrupted"
    assert s["active"]["state"] == "interrupted"
    assert s["saved"] == 1 and s["completed_stages"] == 0
    assert s["models"][1]["blocked"]


def test_pause_marker_does_not_claim_to_stop_active_child(queue):
    q, d, _ = queue
    (q / "PAUSE").touch()
    jobs = [{"script":"run_supergpqa_production.py", "out":str(q), "pid":100, "started":0},
            {"script":"exp16_supergpqa_wildchat.py", "out":str(d), "pid":101, "started":0}]
    assert fake_monitor(q,jobs).snapshot()["state"] == "pause requested"
    assert fake_monitor(q).snapshot()["state"] == "paused"


def test_unrelated_queue_process_does_not_make_this_run_live(queue):
    q, d, _ = queue
    jobs = [{"script":"run_supergpqa_production.py", "out":str(q / "other"), "pid":100, "started":0}]
    assert fake_monitor(q,jobs).snapshot()["state"] == "interrupted"


def test_actual_rows_not_completed_status_determine_collection_progress(queue):
    q, d, _ = queue
    write_rows(d / "rows.jsonl", [row()])
    write_json(q / "status.json", {"active":None, "completed_tasks":[{"model":"e4b", "name":"train", "returncode":0}]})
    s = fake_monitor(q).snapshot()
    assert s["state"] == "failed" and s["tasks"][0]["state"] == "invalid"
    write_rows(d / "rows.jsonl", [row(uid, length=l) for uid in ("q1","q2") for l in (0,1000)])
    s = fake_monitor(q).snapshot()
    assert s["state"] == "complete" and s["completed_stages"] == 1 and s["saved"] == 4


def test_fitting_needs_artifact_not_only_a_success_code():
    assert task_state({}, None, exit_code=0) == "invalid"
    assert task_state({}, None, marker={"n_fitted_heads":8}) == "complete"
    assert task_state({}, None, marker={}) == "invalid"
    assert task_state({}, None, alive=True) == "running"


def test_missing_plan_returns_waiting_without_creating_files(tmp_path):
    path = tmp_path / "absent"
    snapshot = fake_monitor(path).snapshot()
    assert snapshot["state"] == "waiting" and not path.exists()


def test_tail_log_removes_ansi_and_summarizes_json(tmp_path):
    path = tmp_path / "run.log"
    path.write_text('\x1b[31merror\x1b[0m\n' + json.dumps({"done":3,"total":8,"length":1000,"arm":"ZS","status":"ok"}) + '\n')
    lines = tail_log(path)
    assert lines[0] == "error" and "3/8 saved" in lines[1]
    assert all("\x1b" not in x for x in lines)


@pytest.mark.parametrize("width,height,expanded", [(80,24,False),(120,40,False),(160,52,False),(100,24,True)])
def test_responsive_render_does_not_crash_or_claim_detector_training(queue,width,height,expanded):
    from rich.console import Console
    q, d, _ = queue
    write_rows(d / "rows.jsonl", [row()])
    s = fake_monitor(q).snapshot()
    output = io.StringIO()
    console = Console(file=output,width=width,height=height,color_system=None)
    console.print(render_dashboard(s,width=width,height=height,expanded=expanded))
    text = output.getvalue()
    assert "SUPERGPQA" in text and "evaluations saved" in text and "read-only" in text.lower()
    assert "n_fitted_heads" not in text


@pytest.mark.parametrize("width,height", [(80,24), (90,24), (120,40)])
def test_stage_progress_uses_stage_not_model_or_queue_denominator(queue,width,height):
    from rich.console import Console
    q, d, task = queue
    write_rows(d / "rows.jsonl", [row()])
    plan = json.loads((q / "plan.json").read_text())
    plan["tasks"].append({**task, "name":"test", "directory":str(q / "e4b/test"),
        "expected_cells":20, "command":["python","exp16_supergpqa_wildchat.py","--out",str(q / "e4b/test")]})
    write_json(q / "plan.json", plan)
    s = fake_monitor(q).snapshot()
    output = io.StringIO()
    Console(file=output,width=width,height=height,color_system=None).print(
        render_dashboard(s,width=width,height=height))
    lines = output.getvalue().splitlines()
    stage_line = next(line for line in lines if "Stage progress:" in line)
    assert "1 / 4 saved" in stage_line and "25.0%" in stage_line
    assert "1 / 24 evaluations saved" in output.getvalue()
    assert len(lines) <= height


def test_selected_stage_progress_updates_with_navigation(queue):
    from rich.console import Console
    q, d, task = queue
    write_rows(d / "rows.jsonl", [row()])
    plan = json.loads((q / "plan.json").read_text())
    plan["tasks"].append({**task, "name":"test", "directory":str(q / "e4b/test"),
        "expected_cells":20, "command":["python","exp16_supergpqa_wildchat.py","--out",str(q / "e4b/test")]})
    write_json(q / "plan.json", plan)
    output = io.StringIO()
    Console(file=output,width=90,color_system=None).print(
        render_dashboard(fake_monitor(q).snapshot(),width=90,height=24,stage="test"))
    stage_line = next(line for line in output.getvalue().splitlines() if "Stage progress:" in line)
    assert "0 / 20 saved" in stage_line and "0.0%" in stage_line


@pytest.mark.parametrize("state", ["queued", "running", "complete"])
def test_artifact_stage_does_not_invent_a_percentage(queue,state):
    from rich.console import Console
    q, _, _ = queue
    s = fake_monitor(q).snapshot()
    task = {**s["tasks"][0], "name":"fit_supergpqa_heads", "expected":None,
            "progress":None, "state":state}
    s["tasks"].append(task)
    output = io.StringIO()
    Console(file=output,width=90,color_system=None).print(
        render_dashboard(s,width=90,height=24,stage="fit_supergpqa_heads"))
    stage_line = next(line for line in output.getvalue().splitlines() if "Stage progress:" in line)
    assert state in stage_line and "%" not in stage_line
    assert ("completion artifact present" if state == "complete" else "no incremental counter") in stage_line


@pytest.mark.skipif(sys.platform != "linux", reason="PTY smoke check uses Linux terminal controls")
def test_interactive_quit_restores_terminal_and_does_not_edit_queue(queue):
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import termios
    import time
    q, _, _ = queue
    originals = {str(p):p.read_bytes() for p in q.rglob("*") if p.is_file()}
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH",48,140,0,0))
    previous = termios.tcgetattr(slave)
    process = subprocess.Popen([sys.executable,str(ROOT / "scripts/monitor_supergpqa.py"),
        "--queue",str(q),"--interval","0.25","--no-gpu","--color","always"],
        stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ,TERM="xterm-256color"))
    output = b""
    try:
        deadline = time.monotonic()+5
        while b"SUPERGPQA" not in output and time.monotonic()<deadline:
            if select.select([master],[],[],.1)[0]:
                output += os.read(master,65536)
        assert b"SUPERGPQA" in output
        os.write(master,b"n]aq")
        while process.poll() is None and time.monotonic()<deadline:
            if select.select([master],[],[],.1)[0]:
                output += os.read(master,65536)
        assert process.wait(timeout=1)==0
        while select.select([master],[],[],.05)[0]:
            output += os.read(master,65536)
        assert b"Monitor closed" in output
        assert b"\x1b[?1049h" in output and b"\x1b[?1049l" in output
        assert termios.tcgetattr(slave)==previous
        assert {str(p):p.read_bytes() for p in q.rglob("*") if p.is_file()}==originals
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
        os.close(master)
        os.close(slave)
