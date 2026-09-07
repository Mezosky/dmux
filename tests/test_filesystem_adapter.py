import json
import os
from pathlib import Path
import subprocess
import sys

from dmux.adapters.filesystem import FilesystemAdapter
from dmux.monitor import Monitor
from dmux.ui import render_dashboard


NO_GPU = lambda: {"devices": [], "error": None}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def filesystem_plan():
    return {
        "name": "Compatibility Lab",
        "unit": "records",
        "entity_heading": "EXPERIMENT",
        "roster": [
            {"tag": "jsonl", "label": "JSONL training"},
            {"tag": "json", "label": "JSON status"},
            {"tag": "files", "label": "File artifacts"},
            {"tag": "artifact", "label": "Completion marker"},
        ],
        "tasks": [
            {
                "run": "jsonl",
                "stage": "train",
                "directory": "runs/jsonl",
                "expected": 2,
                "log": "train.log",
                "progress": {
                    "type": "jsonl",
                    "path": "metrics.jsonl",
                    "identity": ["epoch"],
                    "status_field": "status",
                    "valid_statuses": ["ok"],
                    "group_field": "split",
                    "group_label": "Dataset split",
                    "expected_by_group": {"train": 2},
                },
            },
            {
                "run": "json",
                "stage": "train",
                "directory": "runs/json",
                "expected": 3,
                "progress": {
                    "type": "json",
                    "path": "progress.json",
                    "current_field": "training.step",
                    "total_field": "training.total",
                },
            },
            {
                "run": "files",
                "stage": "export",
                "directory": "runs/files",
                "expected": 2,
                "progress": {"type": "files", "glob": "checkpoint-*.bin"},
            },
            {
                "run": "artifact",
                "stage": "evaluate",
                "directory": "runs/artifact",
                "completion": {"type": "file", "path": "DONE", "required": ["metrics.json"]},
            },
        ],
    }


def test_filesystem_adapter_reads_json_jsonl_logs_and_artifacts_from_another_cwd(
    tmp_path, monkeypatch
):
    project = tmp_path / "original-ml-project"
    queue = project / "monitor"
    write_json(queue / "plan.json", filesystem_plan())
    jsonl = project / "runs/jsonl"
    jsonl.mkdir(parents=True)
    jsonl.joinpath("metrics.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"epoch": 1, "status": "ok", "split": "train"}),
                json.dumps({"epoch": 2, "status": "ok", "split": "train"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    jsonl.joinpath("train.log").write_text("epoch 2 complete\n", encoding="utf-8")
    write_json(project / "runs/json/progress.json", {"training": {"step": 3, "total": 3}})
    files = project / "runs/files"
    files.mkdir(parents=True)
    files.joinpath("checkpoint-1.bin").touch()
    files.joinpath("checkpoint-2.bin").touch()
    artifact = project / "runs/artifact"
    write_json(artifact / "metrics.json", {"accuracy": 0.8})
    artifact.joinpath("DONE").touch()

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    jobs = [
        {
            "script": "train.py",
            "out": str(jsonl),
            "pid": 123,
            "started": 0,
        }
    ]
    snapshot = Monitor(
        queue,
        project_root=project,
        adapter=FilesystemAdapter(),
        processes=lambda: jobs,
        gpu=NO_GPU,
    ).snapshot(now=10)

    assert snapshot["project_root"] == str(project.resolve())
    assert [task["directory"] for task in snapshot["tasks"]] == [
        str((project / "runs/jsonl").resolve()),
        str((project / "runs/json").resolve()),
        str((project / "runs/files").resolve()),
        str((project / "runs/artifact").resolve()),
    ]
    assert [task["state"] for task in snapshot["tasks"]] == [
        "running",
        "complete",
        "complete",
        "complete",
    ]
    assert snapshot["tasks"][0]["progress"]["saved"] == 2
    assert snapshot["tasks"][0]["progress"]["breakdown"][0]["label"] == "train"
    assert snapshot["recent"] == ["epoch 2 complete"]


def test_jsonl_duplicates_and_partial_rows_never_inflate_progress(tmp_path):
    project = tmp_path / "project"
    queue = project / "monitor"
    plan = filesystem_plan()
    plan["tasks"] = plan["tasks"][:1]
    plan["roster"] = plan["roster"][:1]
    write_json(queue / "plan.json", plan)
    output = project / "runs/jsonl"
    output.mkdir(parents=True)
    complete = json.dumps({"epoch": 1, "status": "ok", "split": "train"})
    output.joinpath("metrics.jsonl").write_bytes(
        (complete + "\n" + complete + "\n" + '{"epoch": 2').encode()
    )
    snapshot = Monitor(
        queue,
        project_root=project,
        adapter=FilesystemAdapter(),
        processes=lambda: [],
        gpu=NO_GPU,
    ).snapshot()
    progress = snapshot["tasks"][0]["progress"]
    assert progress["saved"] == 1
    assert progress["duplicates"] == 1
    assert progress["partial_write"]
    assert snapshot["tasks"][0]["state"] == "invalid"


def test_default_adapter_resolves_relative_paths_from_another_directory(tmp_path, monkeypatch):
    project = tmp_path / "ml-project"
    queue = project / "monitor"
    output = project / "results/run"
    task = {
        "experiment": "classifier",
        "stage": "train",
        "directory": "results/run",
        "expected": 1,
        "progress": {"type": "jsonl", "path": "metrics.jsonl", "identity": ["sample_id"]},
    }
    write_json(queue / "plan.json", {"tasks": [task], "experiments": [{"tag": "classifier"}]})
    output.mkdir(parents=True)
    output.joinpath("metrics.jsonl").write_text('{"sample_id": "image-1"}\n', encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    snapshot = Monitor(queue, project_root=project, processes=lambda: [], gpu=NO_GPU).snapshot()
    assert snapshot["tasks"][0]["directory"] == str(output.resolve())
    assert snapshot["tasks"][0]["state"] == "complete"


def test_plan_can_declare_project_root_for_portable_launch(tmp_path, monkeypatch):
    project = tmp_path / "declared-project"
    queue = tmp_path / "external-monitor"
    plan = filesystem_plan()
    plan["project_root"] = str(project)
    plan["tasks"] = plan["tasks"][1:2]
    plan["roster"] = plan["roster"][1:2]
    write_json(queue / "plan.json", plan)
    write_json(project / "runs/json/progress.json", {"training": {"step": 3, "total": 3}})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    snapshot = Monitor(
        queue, adapter=FilesystemAdapter(), processes=lambda: [], gpu=NO_GPU
    ).snapshot()
    assert snapshot["project_root"] == str(project.resolve())
    assert snapshot["tasks"][0]["state"] == "complete"


def test_numeric_task_with_completion_requires_both_count_and_artifact(tmp_path):
    project = tmp_path / "project"
    queue = project / "monitor"
    plan = filesystem_plan()
    task = plan["tasks"][1]
    task["completion"] = {"type": "file", "path": "DONE"}
    plan["tasks"] = [task]
    plan["roster"] = plan["roster"][1:2]
    write_json(queue / "plan.json", plan)
    write_json(project / "runs/json/progress.json", {"training": {"step": 3, "total": 3}})
    monitor = Monitor(
        queue, project_root=project, adapter=FilesystemAdapter(), processes=lambda: [], gpu=NO_GPU
    )
    assert monitor.snapshot()["tasks"][0]["state"] == "partial"
    (project / "runs/json/DONE").touch()
    assert monitor.snapshot()["tasks"][0]["state"] == "complete"


def test_file_progress_scan_limit_fails_closed(tmp_path):
    project = tmp_path / "project"
    queue = project / "monitor"
    plan = filesystem_plan()
    task = plan["tasks"][2]
    task["progress"]["max_files"] = 1
    plan["tasks"] = [task]
    plan["roster"] = plan["roster"][2:3]
    write_json(queue / "plan.json", plan)
    output = project / "runs/files"
    output.mkdir(parents=True)
    output.joinpath("checkpoint-1.bin").touch()
    output.joinpath("checkpoint-2.bin").touch()
    snapshot = Monitor(
        queue, project_root=project, adapter=FilesystemAdapter(), processes=lambda: [], gpu=NO_GPU
    ).snapshot()
    assert snapshot["tasks"][0]["state"] == "invalid"
    assert any("scan limit" in warning for warning in snapshot["warnings"])


def test_invalid_declarative_plan_fails_closed_without_creating_outputs(tmp_path):
    queue = tmp_path / "monitor"
    write_json(
        queue / "plan.json",
        {
            "tasks": [
                {
                    "run": "bad",
                    "stage": "train",
                    "directory": "runs/bad",
                    "progress": {"type": "jsonl", "path": "metrics.jsonl", "identity": []},
                }
            ]
        },
    )
    before = {path: path.read_bytes() for path in queue.rglob("*") if path.is_file()}
    snapshot = Monitor(
        queue,
        project_root=tmp_path,
        adapter=FilesystemAdapter(),
        processes=lambda: [],
        gpu=NO_GPU,
    ).snapshot()
    assert snapshot["state"] == "invalid"
    assert "progress.identity" in snapshot["message"]
    assert not (tmp_path / "runs").exists()
    assert {path: path.read_bytes() for path in queue.rglob("*") if path.is_file()} == before


def test_model_zoo_example_generates_heterogeneous_monitorable_experiments(tmp_path):
    root = tmp_path / "demo"
    repository = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(repository / "src"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "dmux",
            "demo",
            "--project-root",
            str(root),
            "--steps",
            "2",
            "--delay",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    snapshot = Monitor(
        root / "monitor",
        project_root=root,
        adapter=FilesystemAdapter(),
        processes=lambda: [],
        gpu=NO_GPU,
    ).snapshot()
    assert snapshot["state"] == "complete"
    assert len(snapshot["models"]) == 4
    assert all(task["state"] == "complete" for task in snapshot["tasks"])
    assert [model["tag"] for model in snapshot["models"]] == [
        "tiny_clip",
        "tiny_llm",
        "tabular",
        "audio",
    ]
    rendered = subprocess.run(
        [
            sys.executable,
            "-m",
            "dmux",
            "snapshot",
            "--adapter",
            "filesystem",
            "--project-root",
            str(root),
            "--queue",
            "monitor",
            "--no-gpu",
            "--color",
            "never",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    assert "DMUX" in rendered and "TINY HETEROGENEOUS MODEL ZOO" in rendered
    assert "Zero-shot image/text matching" in rendered


def test_generic_visual_has_browseable_experiment_tabs(tmp_path):
    from io import StringIO
    from rich.console import Console

    project = tmp_path / "project"
    queue = project / "monitor"
    plan = filesystem_plan()
    write_json(queue / "plan.json", plan)
    for task in plan["tasks"]:
        (project / task["directory"]).mkdir(parents=True, exist_ok=True)
    adapter = FilesystemAdapter()
    snapshot = Monitor(
        queue, project_root=project, adapter=adapter, processes=lambda: [], gpu=NO_GPU
    ).snapshot()
    output = StringIO()
    Console(file=output, width=140, height=40, color_system=None).print(
        render_dashboard(
            snapshot,
            width=140,
            height=40,
            selected="json",
            presentation=adapter.presentation,
        )
    )
    rendered = output.getvalue()
    assert "EXPERIMENTS" in rendered
    assert all(label in rendered for label in ("JSONL training", "JSON status", "File artifacts"))
