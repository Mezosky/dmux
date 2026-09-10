import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from dmux.catalog import CatalogError, ProjectCatalog
from dmux.home import Home
from dmux.metrics import MetricReader, validate_metrics
from dmux.monitor import Monitor
from dmux.plan_schema import validate_plan_schema
from dmux.sqlite_connector import SQLiteReader
from dmux_mlflow import MLflowAdapter


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "mlflow ?#%.db"
    with sqlite3.connect(db) as connection:
        connection.executescript('''
CREATE TABLE runs (run_uuid TEXT PRIMARY KEY, status TEXT);
CREATE TABLE params (run_uuid TEXT, key TEXT, value TEXT, PRIMARY KEY (run_uuid, key));
CREATE TABLE tags (run_uuid TEXT, key TEXT, value TEXT, PRIMARY KEY (run_uuid, key));
CREATE TABLE metrics (run_uuid TEXT, key TEXT, value REAL, timestamp INTEGER, step INTEGER, is_nan INTEGER);
CREATE INDEX metric_window ON metrics(run_uuid, key, step, timestamp);
INSERT INTO runs VALUES ('selected', 'FINISHED'), ('other', 'RUNNING');
INSERT INTO params VALUES ('selected', 'planned', '3');
INSERT INTO tags VALUES ('selected', 'owner', 'demo');
INSERT INTO metrics VALUES ('selected', 'loss', 2, 1000, 0, 0), ('selected', 'loss', 1, 1001, 1, 0),
 ('other', 'loss', 99, 2000, 2, 0), ('selected', 'other_metric', 88, 2001, 3, 0);
''')
    return db


def metric(db):
    return {"label": "Loss", "type": "sqlite", "path": str(db), "table": "metrics",
            "field": "value", "x_field": "step", "null_if": "is_nan", "order_by": ["step", "timestamp"],
            "where": {"run_uuid": "selected", "key": "loss"}}


def project(root, db, **extra):
    out = root / "outputs"
    out.mkdir(exist_ok=True)
    (out / "progress.json").write_text('{"current": 3}')
    plan = {"project_root": str(root), "tasks": [{"experiment": "selected", "directory": "outputs",
        "mlflow": {"database": str(db), "run_id": "selected", "params": ["planned"], "tags": ["owner"],
                   "expected_param": "planned"}, "metrics": [metric(db)],
        "progress": {"type": "json", "path": "progress.json"}, **extra}]}
    (root / "plan.json").write_text(json.dumps(plan))
    validate_plan_schema(plan)
    return Monitor(root, adapter=MLflowAdapter(), processes=lambda: [], gpu=lambda: {"devices": []})


def files(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_sqlite_metadata_and_lazy_metrics_are_read_only(database, tmp_path, monkeypatch):
    mon = project(tmp_path, database)
    before = files(tmp_path)
    calls = []
    original = SQLiteReader.read
    monkeypatch.setattr(SQLiteReader, "read", lambda self, *args, **kw:
                        calls.append(kw["table"]) or original(self, *args, **kw))
    snapshot = mon.snapshot()
    task = snapshot["tasks"][0]
    assert task["state"] == "complete" and task["expected"] == 3 and task["pid"] is None
    assert task["metadata"] == {"params/planned": "3", "tags/owner": "demo"}
    assert "metrics" not in calls
    data = MetricReader().read(task["directory"], task["metrics"][0])
    assert data["points"] == [(0, 2), (1, 1)] and data["error"] is None
    assert files(tmp_path) == before


@pytest.mark.parametrize("status,state", [("RUNNING", "interrupted"), ("SCHEDULED", "partial"),
                                         ("FAILED", "failed"), ("KILLED", "failed"), ("broken", "invalid")])
def test_sqlite_status_never_proves_liveness(database, tmp_path, status, state):
    mon = project(tmp_path, database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE runs SET status=? WHERE run_uuid='selected'", (status,))
    assert mon.snapshot()["tasks"][0]["state"] == state


def test_sqlite_metrics_cache_commit_and_nonfinite_handling(database, tmp_path, monkeypatch):
    reader = MetricReader()
    config = metric(database)
    reader.read(tmp_path, config)
    original = SQLiteReader.read
    monkeypatch.setattr(SQLiteReader, "read", lambda *a, **k: pytest.fail("unchanged DB was queried"))
    assert reader.read(tmp_path, config)["latest"] == 1
    monkeypatch.setattr(SQLiteReader, "read", original)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO metrics VALUES ('selected', 'loss', 0, 1002, 2, 1)")
        connection.execute("INSERT INTO metrics VALUES ('selected', 'loss', .5, 1003, 3, 0)")
    data = reader.read(tmp_path, config)
    assert data["latest"] == .5 and len(data["points"]) == 3 and data["warnings"]
    database.unlink()
    assert reader.read(tmp_path, config)["latest"] is None
    assert not database.exists()


def test_sqlite_read_bounds_and_filters(database, tmp_path):
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO metrics VALUES ('selected', 'loss', ?, ?, ?, 0)",
                               [(i, i + 2000, i + 5) for i in range(1000)])
    data = MetricReader().read(tmp_path, metric(database))
    assert data["limited"] and len(data["points"]) == 256 and data["latest"] == 999
    reader = SQLiteReader()
    reader.max_instructions = 0
    with pytest.raises(ValueError, match="interrupted"):
        reader.read(database, table="metrics", columns=["value"], where={}, order_by=["value"], limit=257)
    with pytest.raises(ValueError, match="identifiers"):
        reader.read(database, table='runs; DROP TABLE runs', columns=["status"], where={})
    assert reader.read(database, table="runs", columns=["status"], where={"run_uuid": "' OR 1=1 --"}) == []


def test_sqlite_wal_rejected_without_sidecar_writes(database, tmp_path):
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE runs SET status='RUNNING'")
        writer.commit()
        before = files(tmp_path)
        data = MetricReader().read(tmp_path, metric(database))
        assert "WAL" in data["error"] and data["latest"] is None
        assert files(tmp_path) == before


def test_missing_database_bad_schema_and_special_file_are_visible(tmp_path):
    db = tmp_path / "missing.db"
    mon = project(tmp_path, db)
    snapshot = mon.snapshot()
    assert snapshot["tasks"][0]["state"] == "invalid" and snapshot["warnings"]
    assert not db.exists()
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    assert mon.snapshot()["tasks"][0]["state"] == "invalid"
    db.unlink()
    import os
    os.mkfifo(db)
    assert "regular file" in MetricReader().read(tmp_path, metric(db))["error"]


@pytest.mark.parametrize("override", [{"table": "metrics;drop"}, {"where": {"x": []}}, {"order_by": []},
                                      {"order_by": "step"}, {"where": {"x": float("inf")}}])
def test_sqlite_metric_validation(override):
    with pytest.raises(ValueError):
        validate_metrics([{**metric(Path("mlflow.db")), **override}])


def test_registered_adapter_preserved_and_broken_plugin_does_not_block_home(database, tmp_path, monkeypatch):
    project(tmp_path, database)
    monkeypatch.setattr("dmux.catalog.load_adapter", lambda name: MLflowAdapter())
    catalog = ProjectCatalog()
    first = catalog.add(tmp_path, adapter="mlflow")
    again = catalog.add(tmp_path, name="renamed")
    assert again["adapter"] == "mlflow" and again["id"] == first["id"]
    home = Home(catalog, sampler=SimpleNamespace(processes=lambda adapter: [], gpu=lambda: {"devices": []}))
    monkeypatch.setattr("dmux.home.load_adapter", lambda name: MLflowAdapter())
    home.refresh(force=True)
    assert home.rows()[0]["state"] == "complete" and home.rows()[0]["stages"] == "1/1"
    monkeypatch.setattr("dmux.home.load_adapter", lambda name: (_ for _ in ()).throw(ImportError("plugin missing")))
    home.monitors.clear()
    home.refresh(force=True)
    assert home.rows()[0]["state"] == "unavailable" and "plugin missing" in home.rows()[0]["label"]


def test_old_registration_defaults_and_invalid_adapter_fail_closed(tmp_path):
    catalog = ProjectCatalog()
    catalog.path.parent.mkdir(parents=True)
    entry = {"id": "old", "name": "old", "plan_dir": str(tmp_path), "project_root": str(tmp_path)}
    catalog.path.write_text(json.dumps({"version": 1, "projects": [entry]}))
    assert catalog.read()[0].get("adapter", "filesystem") == "filesystem"
    for value in (None, [], ""):
        catalog.path.write_text(json.dumps({"version": 1, "projects": [{**entry, "adapter": value}]}))
        with pytest.raises(CatalogError, match="adapter"):
            catalog.read()


@pytest.mark.skipif(importlib.util.find_spec("mlflow") is None or importlib.util.find_spec("sqlalchemy") is None,
                    reason="optional MLflow SDK database dependencies not installed")
def test_real_sdk_sqlite_layout(tmp_path):
    from mlflow import MlflowClient
    db = tmp_path / "mlflow.db"
    client = MlflowClient(tracking_uri=f"sqlite:///{db}")
    experiment = client.create_experiment("tiny", artifact_location=(tmp_path / "artifacts").as_uri())
    run_id = client.create_run(experiment).info.run_id
    client.log_param(run_id, "planned", "3")
    client.set_tag(run_id, "owner", "demo")
    client.log_metric(run_id, "loss", 2, timestamp=1000, step=0)
    client.log_metric(run_id, "loss", 1, timestamp=1001, step=1)
    client.set_terminated(run_id)
    mon = project(tmp_path, db)
    plan = json.loads((tmp_path / "plan.json").read_text())
    plan["tasks"][0]["mlflow"]["run_id"] = run_id
    plan["tasks"][0]["metrics"][0]["where"]["run_uuid"] = run_id
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    before = files(tmp_path)
    task = mon.snapshot()["tasks"][0]
    assert task["state"] == "complete"
    assert MetricReader().read(task["directory"], task["metrics"][0])["points"] == [(0, 2), (1, 1)]
    assert files(tmp_path) == before
