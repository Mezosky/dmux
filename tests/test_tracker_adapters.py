import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dmux.connectors import TextCache
from dmux.diagnostics import diagnose
from dmux.metrics import MetricReader, validate_metrics
from dmux.monitor import Monitor
from dmux.plan_schema import validate_plan_schema
from dmux.registry import adapter_names, adapter_statuses, load_adapter
from dmux_mlflow import MLflowAdapter


@pytest.fixture
def installed_mlflow_entry():
    if "mlflow" not in adapter_names():
        pytest.skip('Editable install lacks mlflow entry point; run python -m pip install -e ".[dev]"')


def write_plan(root, **task_fields):
    directory = root / "monitor"
    directory.mkdir(exist_ok=True)
    plan = {"project_root": str(root), "tasks": [
        {"experiment": "run-id", "stage": "train", "directory": "mlruns/0/run-id", **task_fields}
    ]}
    (directory / "plan.json").write_text(json.dumps(plan))
    return directory


def run_directory(root, status=1):
    run = root / "mlruns/0/run-id"
    run.mkdir(parents=True, exist_ok=True)
    (run / "meta.yaml").write_text(f"artifact_uri: file:///unused\nend_time: null\nstatus: {status}\ntags: []\n")
    return run


def monitor(directory, processes=lambda: []):
    return Monitor(directory, adapter=MLflowAdapter(), processes=processes, gpu=lambda: {"devices": []})


def text_metric(**fields):
    return {"label": "Loss", "type": "whitespace", "path": "metrics/loss",
            "columns": ["timestamp", "value", "step"], "field": "value", "x_field": "step", **fields}


@pytest.mark.parametrize("status,expected_state", [(1, "interrupted"), (2, "queued"), (3, "complete"),
                                                   (4, "failed"), (5, "failed")])
def test_mlflow_status_is_advisory_and_artifact_only(tmp_path, status, expected_state):
    run = run_directory(tmp_path, status)
    directory = write_plan(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = monitor(directory).snapshot()
    task = snapshot["tasks"][0]
    assert task["state"] == expected_state
    assert task["pid"] is None and task["progress"] is None and task["expected"] is None
    assert not task["counted"] and snapshot["expected"] is None
    process = {"pid": 123, "started": 1, "script": "train.py", "out": str(run), "command": ["train.py"]}
    write_plan(tmp_path, process={"script": "train.py", "output_flag": "--run-dir"})
    assert monitor(directory, processes=lambda: [process]).snapshot()["tasks"][0]["state"] == "running"
    # Observation itself did not write any result file.
    assert (run / "meta.yaml").read_bytes() == before[run / "meta.yaml"]


@pytest.mark.parametrize("content", ["", "status: RUNNING\n", "status: true\n", "status: 99\n",
    "status: [3]\n", "status: 3\nstatus: 4\n", 'status: 3\n"status": 4\n',
    "status: 3\n---\nstatus: 4\n", "status: 3\n<<: *other\n", "status: 3\ninvalid YAML\n"])
def test_bad_metadata_cannot_reuse_cached_completion(tmp_path, content):
    run = run_directory(tmp_path, 3)
    mon = monitor(write_plan(tmp_path))
    assert mon.snapshot()["tasks"][0]["state"] == "complete"
    (run / "meta.yaml").write_text(content)
    snapshot = mon.snapshot()
    assert snapshot["tasks"][0]["state"] == "invalid"
    assert any("meta.yaml" in w for w in snapshot["warnings"])


def test_missing_metadata_and_params_remain_visible(tmp_path):
    run = run_directory(tmp_path, 3)
    mon = monitor(write_plan(tmp_path, mlflow={"params": ["missing"]}))
    assert any("params/missing" in w for w in mon.snapshot()["warnings"])
    (run / "meta.yaml").unlink()
    snapshot = mon.snapshot()
    assert snapshot["tasks"][0]["state"] == "queued"
    assert any("not generated yet" in w for w in snapshot["warnings"])


def test_explicit_total_param_and_progress_are_separate_from_metrics(tmp_path, monkeypatch):
    run = run_directory(tmp_path, 1)
    (run / "params").mkdir()
    (run / "params/count").write_text("10")
    (run / "params/epochs").write_text("999")
    (run / "tags").mkdir()
    (run / "tags/owner").write_text("research")
    (run / "progress.json").write_text('{"current": 10}')
    directory = write_plan(tmp_path, progress={"type": "json", "path": "progress.json"},
                           mlflow={"expected_param": "count", "params": ["epochs"], "tags": ["owner"]},
                           metrics=[text_metric()])
    mon = monitor(directory)
    monkeypatch.setattr(MetricReader, "read", lambda *a: pytest.fail("snapshot read a result history"))
    task = mon.snapshot()["tasks"][0]
    assert task["state"] == "interrupted"  # Reaching the total does not finish a RUNNING tracker.
    assert task["expected"] == task["progress"]["saved"] == 10
    assert task["metadata"] == {"params/epochs": "999", "tags/owner": "research"}
    (run / "meta.yaml").write_text("status: 3\n")
    assert mon.snapshot()["tasks"][0]["state"] == "complete"
    (run / "params/count").write_text("broken")
    task = mon.snapshot()["tasks"][0]
    assert task["state"] == "invalid" and task["expected"] is None


def test_mlflow_process_matching_and_paths_use_explicit_root(tmp_path, monkeypatch):
    run = run_directory(tmp_path)
    directory = write_plan(tmp_path, process={"script": "train.py", "output_flag": "--run-dir"})
    monkeypatch.chdir(tmp_path.parent)
    mon = monitor(directory)
    snapshot = mon.snapshot()
    assert snapshot["tasks"][0]["directory"] == str(run)
    assert mon.adapter.process_destination("train.py", ["train.py", "--run-dir=mlruns/0/run-id"], tmp_path) == run
    assert not mon.adapter.tracked_scripts - {"train.py"}


@pytest.mark.parametrize("config", [[], {"params": "epochs"}, {"tags": ["../secret"]}, {"tags": ["/secret"]},
    {"params": ["."]}, {"params": [True]}, {"expected_param": "epochs"}, {"server": "https://example.com"}])
def test_invalid_mlflow_config_rejected(tmp_path, config):
    assert monitor(write_plan(tmp_path, mlflow=config)).snapshot()["state"] == "invalid"


def test_whitespace_metrics_are_bounded_committed_cached_and_strict(tmp_path, monkeypatch):
    source = tmp_path / "metrics/loss"
    source.parent.mkdir()
    source.write_bytes(b"1000 2 0\n1001 1 1\n1002 0 1\n1003 NaN 2\nbad\n\xff\n1004 .5 3 extra\n1005 .4 4")
    reader = MetricReader()
    data = reader.read(tmp_path, text_metric())
    assert data["points"] == [(0, 2), (1, 1)]
    assert len(data["warnings"]) == 3 and not data["error"]
    with source.open("ab") as handle:
        handle.write(b"\n")
    assert reader.read(tmp_path, text_metric())["latest"] == .4
    stamp, cached = next(iter(reader.cache.values()))
    reader.read(tmp_path, text_metric())
    assert next(iter(reader.cache.values()))[1] is cached
    source.write_text("".join(f"{i} {i} {i}\n" for i in range(1000)))
    reader.max_bytes = 512
    data = reader.read(tmp_path, text_metric())
    assert data["latest"] == 999 and data["limited"] and len(data["points"]) <= 256
    replacement = tmp_path / "replacement"
    replacement.write_text("1000 .1 0\n")
    replacement.replace(source)
    assert reader.read(tmp_path, text_metric())["points"] == [(0, .1)]
    source.unlink()
    assert reader.read(tmp_path, text_metric())["latest"] is None


def test_metric_columns_are_part_of_cache_identity(tmp_path):
    (tmp_path / "data").write_text("1 2 3\n")
    reader = MetricReader()
    assert reader.read(tmp_path, text_metric(path="data"))["latest"] == 2
    assert reader.read(tmp_path, text_metric(path="data", columns=["value", "timestamp", "step"]))["latest"] == 1


@pytest.mark.parametrize("fields", [{"columns": []}, {"columns": ["value", "value"]},
    {"columns": "value"}, {"columns": [True]}, {"field": "missing"}, {"x_field": "missing"}])
def test_whitespace_metric_definition_validation(fields):
    with pytest.raises(ValueError):
        validate_metrics([text_metric(**fields)])


def test_text_cache_bounds_rotation_special_files_and_eviction(tmp_path):
    cache = TextCache(max_bytes=8, max_entries=1)
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("abc")
    assert cache.read(a) == "abc"
    b.write_text("new")
    b.replace(a)
    assert cache.read(a) == "new"
    b.write_text("other")
    assert cache.read(b) == "other" and a not in cache.cache
    b.write_text("x" * 9)
    with pytest.raises(ValueError, match="exceeds"):
        cache.read(b)
    assert b not in cache.cache
    b.unlink()
    os.mkfifo(b)
    with pytest.raises(OSError, match="regular file"):
        cache.read(b)


def fake_entry(name, factory, extras=()):
    return SimpleNamespace(name=name, load=lambda: factory, extras=extras,
                           dist=SimpleNamespace(metadata={"Name": "dmux"}))


def test_registry_discovery_stays_lazy_and_lists_failed_plugins(monkeypatch, capsys):
    from dmux.cli import main

    def missing():
        raise ModuleNotFoundError("No module named 'wandb'", name="wandb")

    entries = [fake_entry("wandb", missing, ["wandb"]), fake_entry("example", MLflowAdapter)]
    monkeypatch.setattr("dmux.registry.entry_points", lambda **kw: entries)
    assert adapter_names() == ("example", "filesystem", "wandb")
    with pytest.raises(ValueError, match=r'Install "dmux\[wandb\]"'):
        load_adapter("wandb")
    assert dict(adapter_statuses())["example"] == "usable"
    main(["adapters"])
    output = capsys.readouterr().out
    assert "filesystem\tusable" in output and "wandb\tunavailable" in output
    entries.append(fake_entry("example", MLflowAdapter))
    with pytest.raises(ValueError, match="Multiple entry points"):
        load_adapter("example")


def test_installed_entry_point_and_doctor(tmp_path, installed_mlflow_entry):
    assert isinstance(load_adapter("mlflow"), MLflowAdapter)
    run_directory(tmp_path, 3)
    directory = write_plan(tmp_path, metrics=[text_metric()])
    validate_plan_schema(json.loads((directory / "plan.json").read_text()))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = diagnose(directory, adapter_name="mlflow", processes=lambda: [])
    assert not report["counts"].get("error")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_core_and_local_adapter_never_import_trackers_or_training_frameworks(installed_mlflow_entry):
    result = subprocess.run([sys.executable, "-c", '''
import sys
import dmux
from dmux.cli import parser
from dmux.registry import load_adapter
parser()
assert "dmux_mlflow" not in sys.modules
load_adapter("mlflow")
for name in ("mlflow", "wandb", "torch", "tensorflow", "jax", "transformers", "vllm", "cupy"):
    assert not any(m == name or m.startswith(name + ".") for m in sys.modules), name
'''], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("example", ["mlflow-plan.json", "wandb-offline-plan.json"])
def test_documented_example_plans_use_only_explicit_files(tmp_path, example):
    from dmux.adapters.filesystem import FilesystemAdapter

    plan = json.loads((Path(__file__).resolve().parents[1] / "examples" / example).read_text())
    plan["project_root"] = str(tmp_path)
    validate_plan_schema(plan)
    FilesystemAdapter().configure(plan)
    source = tmp_path / plan["tasks"][0]["directory"]
    source.mkdir(parents=True)
    if example.startswith("wandb"):
        (source / "wandb-summary.json").write_text('{"_step": 99, "loss": 0.5}')
        (source / "wandb-metadata.json").write_text('{"pid": 123, "state": "running"}')
        (source / "output.log").write_text("logged a result\n")
    else:
        (source / "metrics").mkdir()
        (source / "metrics/loss").write_text("1000 .5 0\n")
        (source / "meta.yaml").write_text("status: 1\n")
    directory = tmp_path / "monitor"
    directory.mkdir()
    (directory / "plan.json").write_text(json.dumps(plan))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    snapshot = Monitor(directory, processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    task = snapshot["tasks"][0]
    assert task["progress"] is None and task["pid"] is None and task["state"] == "queued"
    data = MetricReader().read(source, task["metrics"][0])
    assert data["latest"] == .5 and len(data["points"]) == 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.skipif(importlib.util.find_spec("mlflow") is None, reason="optional dmux-ml[mlflow] SDK not installed")
def test_sdk_file_store_matches_supported_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_LOGGING", "false")
    monkeypatch.setenv("MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING", "false")
    from mlflow import MlflowClient

    store = tmp_path / "sdk-store"
    client = MlflowClient(tracking_uri=store.as_uri())
    experiment = client.create_experiment("tiny")
    run = client.create_run(experiment)
    client.log_metric(run.info.run_id, "loss", 2, timestamp=1000, step=0)
    client.log_metric(run.info.run_id, "loss", 1, timestamp=1001, step=1)
    client.set_terminated(run.info.run_id)
    path = store / experiment / run.info.run_id
    directory = write_plan(tmp_path, directory=str(path), metrics=[text_metric()])
    assert monitor(directory).snapshot()["tasks"][0]["state"] == "complete"
    assert MetricReader().read(path, text_metric())["points"] == [(0, 2), (1, 1)]


@pytest.mark.skipif(sys.platform != "linux" or importlib.util.find_spec("mlflow") is None,
                    reason="Linux demo subprocess test requires the optional MLflow SDK")
@pytest.mark.parametrize("allow_file_store", [None, "false"])
def test_mlflow_demo_generates_real_results_and_refuses_existing_projects(tmp_path, monkeypatch, allow_file_store):
    if allow_file_store is None:
        monkeypatch.delenv("MLFLOW_ALLOW_FILE_STORE", raising=False)
    else:
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", allow_file_store)
    script = Path(__file__).resolve().parents[1] / "examples/run_mlflow_demo.py"
    root = tmp_path / "fresh-demo"
    command = [sys.executable, str(script), "--project-root", str(root), "--steps", "5", "--interval", "0"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "dmux watch --adapter mlflow" in result.stdout
    snapshot = monitor(root / "monitor").snapshot()
    task = snapshot["tasks"][0]
    assert task["state"] == "complete" and task["progress"]["saved"] == task["expected"] == 5
    values = MetricReader().read(task["directory"], task["metrics"][0])["points"]
    assert len(values) == 5 and values[-1][1] < values[0][1]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    rejected = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert rejected.returncode == 2 and "already exists" in rejected.stderr
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
