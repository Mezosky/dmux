"""First-run setup and diagnostic checks use only temporary demo outputs."""
import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from dmux import Monitor
from dmux.adapters.filesystem import FilesystemAdapter
from dmux.discovery import discover_outputs, sample_file
from dmux.onboarding import main as initialize
from dmux.ui import render_dashboard


NO_GPU = lambda: {"devices": [], "error": None}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_init_connects_existing_outputs_without_modifying_them(tmp_path, capsys):
    results = tmp_path / "existing-results"
    write_json(results / "progress.json", {"training": {"step": 3, "total": 10}})
    before = (results / "progress.json").read_bytes()
    initialize(["--project-root", str(tmp_path), "--results-dir", str(results), "--yes",
                "--format", "json", "--source", "progress.json",
                "--current-field", "training.step", "--total-field", "training.total"])
    plan_dir = tmp_path / "monitor"
    monitor = Monitor(plan_dir, processes=lambda: [], gpu=NO_GPU)
    snapshot = monitor.snapshot()
    assert snapshot["saved"] == 3 and snapshot["expected"] == 10
    assert (results / "progress.json").read_bytes() == before
    assert "dmux doctor" in capsys.readouterr().out
    assert FilesystemAdapter().default_queue(tmp_path) == plan_dir


def test_init_dry_run_and_cancel_do_not_create_directories(tmp_path, capsys, monkeypatch):
    initialize(["--project-root", str(tmp_path), "--dry-run", "--format", "json"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["results_dir"] == str(tmp_path / "outputs")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no" if "Create this plan?" in prompt else "")
    initialize(["--project-root", str(tmp_path)])
    assert "Cancelled" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_init_refuses_existing_plan_and_dangling_symlink(tmp_path):
    plan = tmp_path / "monitor" / "plan.json"
    write_json(plan, {"keep": "unchanged"})
    before = plan.read_bytes()
    with pytest.raises(SystemExit) as error:
        initialize(["--project-root", str(tmp_path), "--yes"])
    assert error.value.code == 2 and plan.read_bytes() == before
    other = tmp_path / "other"
    other.mkdir()
    (other / "plan.json").symlink_to(tmp_path / "missing-target")
    with pytest.raises(SystemExit):
        initialize(["--project-root", str(tmp_path), "--plan-dir", "other", "--yes"])
    assert not (tmp_path / "missing-target").exists()


def test_init_prompts_before_using_detected_fields(tmp_path, monkeypatch):
    results = tmp_path / "outputs"
    write_json(results / "progress.json", {"training": {"step": 2, "total": 20}})
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return ""
    monkeypatch.setattr("builtins.input", answer)
    initialize(["--project-root", str(tmp_path), "--results-dir", str(results)])
    plan = json.loads((tmp_path / "monitor" / "plan.json").read_text())
    assert plan["tasks"][0]["progress"]["current_field"] == "training.step"
    assert any("Current counter field [training.step]" in prompt for prompt in prompts)
    assert any("Create this plan?" in prompt for prompt in prompts)


def test_discovery_is_bounded_and_does_not_follow_directory_symlinks(tmp_path):
    write_json(tmp_path / "metrics.json", {"current": 1, "total": 2})
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    files, _ = discover_outputs(tmp_path)
    assert files == [Path("metrics.json")]
    _, limited = discover_outputs(tmp_path, limit=1)
    assert limited
    (tmp_path / "metrics.json").write_text('{"iteration": 19}\n{"iteration": 39}\n')
    kind, record = sample_file(tmp_path / "metrics.json")
    assert kind == "jsonl" and record == {"iteration": 19}


@pytest.mark.parametrize("kind", ["json", "jsonl", "files"])
def test_unknown_totals_show_saved_counts_without_invented_percentages(tmp_path, kind):
    results = tmp_path / "outputs"
    results.mkdir()
    if kind == "json":
        write_json(results / "progress.json", {"current": 3})
        config = {"type": "json", "path": "progress.json"}
    elif kind == "jsonl":
        (results / "records.jsonl").write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n')
        config = {"type": "jsonl", "path": "records.jsonl"}
    else:
        for index in range(3):
            (results / f"checkpoint-{index}.bin").touch()
        config = {"type": "files", "glob": "checkpoint-*.bin"}
    plan_dir = tmp_path / "monitor"
    write_json(plan_dir / "plan.json", {"project_root": str(tmp_path), "tasks": [{
        "experiment": "demo", "stage": "train", "directory": "outputs", "progress": config,
    }]})
    snapshot = Monitor(plan_dir, processes=lambda: [], gpu=NO_GPU).snapshot()
    assert snapshot["saved"] == 3 and snapshot["expected"] is None
    assert snapshot["tasks"][0]["state"] == "partial"
    stream = io.StringIO()
    Console(file=stream, width=100, color_system=None).print(render_dashboard(snapshot, height=24))
    text = stream.getvalue()
    assert "3 items saved" in text and "total unknown" in text
    assert "%" not in text


@pytest.mark.parametrize("config", [
    {"current_field": []}, {"total_field": {}}, {"type": "jsonl", "identity": []},
])
def test_invalid_connector_fields_fail_closed(tmp_path, config):
    plan_dir = tmp_path / "monitor"
    write_json(plan_dir / "plan.json", {"tasks": [{
        "experiment": "bad", "directory": str(tmp_path), "progress": {
            "type": "json", "path": "progress.json", **config,
        },
    }]})
    snapshot = Monitor(plan_dir, processes=lambda: [], gpu=NO_GPU).snapshot()
    assert snapshot["state"] == "invalid"


def test_unknown_stage_prevents_invented_aggregate_total_but_not_known_stage_progress(tmp_path):
    write_json(tmp_path / "known.json", {"current": 2, "total": 4})
    write_json(tmp_path / "unknown.json", {"current": 3})
    write_json(tmp_path / "plan.json", {"project_root": str(tmp_path), "tasks": [
        {"experiment": "mixed", "stage": name, "directory": ".", "progress": {"type": "json", "path": f"{name}.json"}}
        for name in ("known", "unknown")
    ]})
    snapshot = Monitor(tmp_path, processes=lambda: [], gpu=NO_GPU).snapshot()
    assert snapshot["saved"] == 5 and snapshot["expected"] is None
    assert snapshot["experiments"][0]["expected"] is None
    stream = io.StringIO()
    Console(file=stream, width=100, color_system=None).print(render_dashboard(snapshot, stage="known", height=24))
    assert "50.0%" in stream.getvalue() and "5 items saved · total unknown" in stream.getvalue()


def test_starter_example_is_a_working_minimal_config(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples" / "plan.json"
    plan = json.loads(source.read_text())
    plan["project_root"] = str(tmp_path)
    write_json(tmp_path / "plan.json", plan)
    write_json(tmp_path / "outputs" / "progress.json", {"current": 3, "total": 10})
    snapshot = Monitor(tmp_path, processes=lambda: [], gpu=NO_GPU).snapshot()
    assert snapshot["saved"] == 3 and snapshot["expected"] == 10
