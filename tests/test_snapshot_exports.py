from copy import deepcopy
from importlib.resources import files
import json

import pytest
from jsonschema import Draft202012Validator

from dmux.cli import main
from dmux.monitor import Monitor
from dmux.snapshots import export_snapshot


def validate(name, value):
    schema = json.loads(files("dmux").joinpath("schemas", name + "-v2.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize("plan", [None, {"tasks": [123]}, {"tasks": []}, {"tasks": [
    {"experiment": "one", "stage": "train", "directory": ".", "expected": None,
     "progress": {"type": "json", "path": "counter.json"}},
    {"experiment": "two", "stage": "export", "directory": ".",
     "completion": {"type": "file", "path": "model.bin"}}]}])
def test_snapshot_v2_schema_and_alias_removal_without_mutation(tmp_path, plan):
    if plan is not None:
        (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "counter.json").write_text('{"current": 2}')
    raw = Monitor(tmp_path, project_root=tmp_path, processes=lambda: [], gpu=lambda: {"devices": []}).snapshot()
    if raw["tasks"]:
        raw["active"] = raw["tasks"][0]
    before = deepcopy(raw)
    public = export_snapshot(raw)
    validate("snapshot", public)
    assert "models" not in public and "queue" not in public
    for task in [*public["tasks"], *([public["active"]] if public.get("active") else [])]:
        assert "model" not in task and "name" not in task
    assert raw == before and export_snapshot(raw, version=1) == before
    if public["tasks"]:
        assert public["expected"] is None and public["tasks"][0]["expected"] is None
    with pytest.raises(ValueError):
        export_snapshot(raw, version=3)


def test_json_cli_schema_selection_and_global_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dmux.tmux.TmuxNavigator.snapshot", lambda *a: {})
    (tmp_path / "plan.json").write_text('{"tasks": []}')
    for version in (1, 2):
        main(["json", "--plan-dir", str(tmp_path), "--no-gpu", "--schema-version", str(version)])
        scoped = json.loads(capsys.readouterr().out)
        assert scoped["schema_version"] == version
        assert ("models" in scoped) == (version == 1)
        if version == 2:
            validate("snapshot", scoped)
        main(["home", "--json", "--no-gpu", "--schema-version", str(version)])
        home = json.loads(capsys.readouterr().out)
        assert ("schema_version" in home) == (version == 2)
        if version == 2:
            validate("home", home)


def test_home_schema_with_adapter_registration(tmp_path, monkeypatch, capsys):
    from dmux.catalog import ProjectCatalog
    (tmp_path / "plan.json").write_text('{"tasks": [{"experiment": "sample"}]}')
    ProjectCatalog().add(tmp_path)
    monkeypatch.setattr("dmux.system.HostSampler.processes", lambda *a: [])
    main(["home", "--json", "--no-gpu"])
    validate("home", json.loads(capsys.readouterr().out))
