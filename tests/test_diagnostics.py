"""Demo-only connection validation: no real research projects or model downloads."""
import json
import os
from pathlib import Path

import pytest

from dmux.cli import main
from dmux.demo import main as demo
from dmux.diagnostics import diagnose
from dmux.monitor import Monitor
from dmux.onboarding import main as initialize


def fixture_plan(root, task=None):
    directory = root / "monitor"
    directory.mkdir()
    (root / "outputs").mkdir()
    definition = {"project_root": str(root), "disk_warning_gib": 0,
                  "tasks": [{"experiment": "demo", "stage": "train", "directory": "outputs",
                             "progress": {"type": "json", "path": "progress.json"}, **(task or {})}]}
    (directory / "plan.json").write_text(json.dumps(definition))
    return directory


def test_doctor_missing_plan_does_not_create_it(tmp_path):
    report = diagnose(tmp_path / "missing", processes=lambda: [])
    assert report["exit_code"] == 2 and report["checks"][0]["code"] == "plan_missing"
    assert list(tmp_path.iterdir()) == []


def test_doctor_checks_all_four_demo_formats_from_another_directory(tmp_path, monkeypatch):
    project = tmp_path / "demo project"
    demo(["--quick", "--project-root", str(project), "--steps", "2"])
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("doctor must not launch commands"))
    report = diagnose(project / "monitor", processes=lambda: [])
    assert report["exit_code"] == 0, report
    assert {c["scope"] for c in report["checks"]} >= {
        "tiny_clip/evaluate", "tiny_llm/train", "tabular/train", "audio/evaluate"}
    assert any(c["code"] == "file_count" and "2 files match" in c["message"] for c in report["checks"])
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


def test_doctor_explains_wrong_fields_and_format(tmp_path):
    directory = fixture_plan(tmp_path)
    source = tmp_path / "outputs" / "progress.json"
    source.write_text('{"training": {"step": 3, "total": 10}}')
    report = diagnose(directory, processes=lambda: [])
    assert report["exit_code"] == 2
    assert any(c["code"] == "counter_fields" and "training.step" in c["hint"] for c in report["checks"])
    source.write_text('{"iteration": 1}\n{"iteration": 2}\n')
    report = diagnose(directory, processes=lambda: [])
    assert any(c["code"] == "format_mismatch" for c in report["checks"])


def test_doctor_jsonl_integrity_and_partial_write(tmp_path):
    directory = fixture_plan(tmp_path, {"expected": 2,
        "progress": {"type": "jsonl", "path": "metrics.jsonl", "identity": ["sample"]}})
    source = tmp_path / "outputs" / "metrics.jsonl"
    source.write_text('{"sample": 1}\n{"sample": 1}\n{"wrong": 2}\nnot json\n{"sample": 2}')
    report = diagnose(directory, processes=lambda: [])
    checks = {c["code"]: c for c in report["checks"]}
    assert report["exit_code"] == 2
    assert {"duplicates", "malformed", "partial_write"} <= checks.keys()
    assert "1 committed unique" in checks["record_count"]["message"]


def test_doctor_distinguishes_pending_outputs_and_unknown_totals(tmp_path):
    directory = fixture_plan(tmp_path, {"log": "train.log", "process": {"script": "train.py", "output_flag": "--results"}})
    report = diagnose(directory, processes=lambda: [])
    assert report["exit_code"] == 1 and "error" not in report["counts"]
    checks = {c["code"]: c for c in report["checks"]}
    assert {"source_missing", "unknown_total", "process_unmatched"} <= checks.keys()
    assert "--results" in checks["process_unmatched"]["hint"]
    assert str(tmp_path / "outputs") in checks["process_unmatched"]["hint"]


@pytest.mark.parametrize("mutation", [
    {"project_root": []}, {"queue_process": []}, {"name": []}, {"tasks": [3]},
    {"tasks": [{"process": []}]}, {"tasks": [{"stage": [], "directory": "outputs"}]},
])
def test_doctor_handles_bad_plan_schema_without_tracebacks(tmp_path, mutation):
    directory = fixture_plan(tmp_path)
    path = directory / "plan.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **mutation}))
    assert diagnose(directory, processes=lambda: [])["exit_code"] == 2


def test_doctor_does_not_block_on_special_file(tmp_path):
    directory = fixture_plan(tmp_path)
    os.mkfifo(tmp_path / "outputs" / "progress.json")
    report = diagnose(directory, processes=lambda: [])
    assert report["exit_code"] == 2 and report["checks"][0]["code"] == "not_a_file"


@pytest.mark.parametrize("exit_code,content", [(0, '{"current": 1}'), (1, None), (2, 'broken')])
def test_doctor_cli_json_exit_codes(tmp_path, capsys, exit_code, content):
    directory = fixture_plan(tmp_path)
    if content:
        (tmp_path / "outputs" / "progress.json").write_text(content)
    args = ["doctor", "--plan-dir", str(directory), "--json"]
    if exit_code:
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == exit_code
    else:
        main(args)
    assert json.loads(capsys.readouterr().out)["exit_code"] == exit_code


def test_doctor_human_output(tmp_path, capsys):
    directory = fixture_plan(tmp_path)
    (tmp_path / "outputs" / "progress.json").write_text('{"current": 3}')
    main(["doctor", "--plan-dir", str(directory)])
    text = capsys.readouterr().out
    assert "DMUX / CONNECTION CHECK" in text and "0 errors" in text and "read-only" in text


@pytest.mark.parametrize("kind,source,expected", [
    ("json", "progress.json", None), ("jsonl", "metrics.jsonl", "8"),
    ("files", "checkpoint-*.json", "8"), ("completion", "DONE", None),
])
def test_init_templates_connect_demo_output_types(tmp_path, kind, source, expected):
    args = ["--project-root", str(tmp_path), "--yes", "--format", kind, "--source", source]
    if expected:
        args.extend(["--expected", expected])
    initialize(args)
    snapshot = Monitor(tmp_path / "monitor", processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    assert snapshot["state"] == "idle" and len(snapshot["tasks"]) == 1
    assert not (tmp_path / "outputs").exists()


def test_init_noninteractive_requires_explicit_yes(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        main(["init", "--project-root", str(tmp_path)])
    assert error.value.code == 2 and list(tmp_path.iterdir()) == []


def test_process_matching_uses_stage_script_and_supports_equal_argument(tmp_path):
    from dmux.adapters.filesystem import FilesystemAdapter
    from dmux.adapters.base import command_option

    directory = fixture_plan(tmp_path, {"process": {"script": "train.py", "output_flag": "--results"}})
    out = tmp_path / "outputs"
    assert command_option(["python", "train.py", "--results=outputs"], "--results") == "outputs"
    adapter = FilesystemAdapter()
    adapter.configure(json.loads((directory / "plan.json").read_text()))
    assert adapter.process_destination("train.py", ["python", "train.py", "--results=outputs"], tmp_path) == out
    jobs = [{"pid": 123, "script": "evaluate.py", "out": str(out), "started": 1}]
    snapshot = Monitor(directory, processes=lambda: jobs, gpu=lambda: {"devices": []}).snapshot()
    assert snapshot["tasks"][0]["pid"] is None


def test_doctor_future_jsonl_and_bounded_batch(tmp_path, monkeypatch):
    from dmux.connectors import IncrementalJsonlReader

    directory = fixture_plan(tmp_path, {"progress": {"type": "jsonl", "path": "metrics.jsonl"}})
    assert diagnose(directory, processes=lambda: [])["exit_code"] == 1
    source = tmp_path / "outputs" / "metrics.jsonl"
    source.write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n')
    original = IncrementalJsonlReader.read
    def bounded(reader):
        reader.max_bytes_per_read = 10
        return original(reader)
    monkeypatch.setattr(IncrementalJsonlReader, "read", bounded)
    report = diagnose(directory, processes=lambda: [])
    assert any(c["code"] == "read_budget" for c in report["checks"])


def test_doctor_rejects_special_scheduler_file_even_without_tasks(tmp_path):
    directory = fixture_plan(tmp_path)
    (directory / "plan.json").write_text('{"tasks": []}')
    os.mkfifo(directory / "status.json")
    assert diagnose(directory, processes=lambda: [])["exit_code"] == 2


def test_wizard_reconnects_to_all_demo_results_without_changing_them(tmp_path, monkeypatch):
    producer = tmp_path / "producer"
    demo(["--quick", "--project-root", str(producer), "--steps", "2"])
    before = {p: p.read_bytes() for p in producer.rglob("*") if p.is_file()}
    layouts = [
        ("tiny_clip", ["--format", "jsonl", "--source", "predictions.jsonl", "--identity", "sample_id", "--expected", "2"]),
        ("tiny_llm", ["--format", "json", "--source", "progress.json", "--current-field", "training.epoch", "--total-field", "training.total_epochs"]),
        ("tabular", ["--format", "files", "--source", "checkpoint-*.json", "--expected", "2"]),
        ("audio", ["--format", "completion", "--source", "DONE"]),
    ]
    for name, flags in layouts:
        consumer = tmp_path / f"connection-{name}"
        consumer.mkdir()
        main(["init", "--project-root", str(consumer), "--results-dir", str(producer / "runs" / name),
              "--experiment", name, "--yes", *flags])
        monkeypatch.chdir(tmp_path)
        assert diagnose(consumer / "monitor", processes=lambda: [])["exit_code"] == 0
        snapshot = Monitor(consumer / "monitor", processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
        assert snapshot["state"] == "complete"
    assert {p: p.read_bytes() for p in producer.rglob("*") if p.is_file()} == before
