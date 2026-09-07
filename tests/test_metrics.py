import io
import json
import math
import os

import pytest
from rich.console import Console

from dmux.demo import main as demo
from dmux.experiment_view import render_detail
from dmux.metrics import MetricReader, sparkline, validate_metrics
from dmux.monitor import Monitor
from dmux.ui import render_dashboard


def metric(**values):
    return {"label": "Loss", "type": "jsonl", "path": "history.jsonl", "field": "loss", "x_field": "step", "goal": "min", **values}


def test_jsonl_metrics_commit_and_integrity_do_not_invent_points(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"step":1,"loss":3}\n{"step":2,"loss":2}\n{"step":2,"loss":0}\n'
                    '{"step":3,"loss":NaN}\nnot-json\n{"step":4,"loss":1}')
    reader = MetricReader()
    data = reader.read(tmp_path, metric())
    assert data["points"] == [(1.0, 3.0), (2.0, 2.0)]
    assert data["latest"] == data["best"] == 2
    assert len(data["warnings"]) == 3
    with path.open("a") as handle:
        handle.write("\n")
    assert reader.read(tmp_path, metric())["latest"] == 1


@pytest.mark.parametrize("kind,filename,content,extras", [
    ("json", "summary.json", '{"loss":0.4}', {"x_field": ""}),
    ("json", "summary.json", '{"history":[{"step":1,"loss":2},{"step":2,"loss":0.4}]}', {"records_field": "history"}),
    ("csv", "history.csv", 'step,loss\n1,2\n2,0.4\n', {}),
    ("csv", "history.csv", 'step,train.loss\n1,2\n2,0.4\n', {"field": "train.loss"}),
])
def test_result_formats(tmp_path, kind, filename, content, extras):
    (tmp_path / filename).write_text(content)
    data = MetricReader().read(tmp_path, metric(type=kind, path=filename, **extras))
    assert data["latest"] == .4 and not data["error"]


def test_bounded_history_and_cache_shared_between_metrics(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    path.write_text("".join(json.dumps({"step": i, "loss": i, "accuracy": i / 1000}) + "\n" for i in range(1000)))
    reader = MetricReader()
    reader.max_bytes = 1024
    data = reader.read(tmp_path, metric())
    assert data["limited"] and len(data["points"]) <= 256 and data["latest"] == 999
    # Cached parsed records serve multiple fields, not a reread per metric.
    monkeypatch.setattr("dmux.metrics.json.loads", lambda *args: pytest.fail("unchanged file was reparsed"))
    accuracy = reader.read(tmp_path, metric(field="accuracy", goal="max", scale=100))
    assert accuracy["latest"] == pytest.approx(99.9)
    assert len(reader.cache) == 1


def test_rotation_missing_and_invalid_json_drop_stale_result_values(tmp_path):
    path = tmp_path / "summary.json"
    config = metric(type="json", path=path.name, x_field="")
    reader = MetricReader()
    path.write_text('{"loss":2}')
    assert reader.read(tmp_path, config)["latest"] == 2
    path.write_text('{"loss":')
    broken = reader.read(tmp_path, config)
    assert broken["error"] and broken["latest"] is None
    path.unlink()
    assert reader.read(tmp_path, config)["error"] == "not generated yet"
    os.mkfifo(path)
    assert "regular file" in reader.read(tmp_path, config)["error"]


@pytest.mark.parametrize("values", [[], [1], [1, 1, 1], [1, 2, 3], [-1e308, 0, 1e308], list(range(1000))])
def test_sparkline_is_small_and_supports_constant_and_extreme_values(values):
    line = sparkline(values, width=20)
    assert len(line) <= 20
    if len(set(values)) == 1:
        assert set(line) == {"▄"}


@pytest.mark.parametrize("invalid", [
    {"field": None}, {"type": "pickle"}, {"precision": 12}, {"scale": math.inf}, {"goal": "guess"},
])
def test_invalid_metric_schema_is_rejected(invalid):
    with pytest.raises(ValueError):
        validate_metrics([metric(**invalid)])


def test_metric_pages_wrap_without_overlapping_offsets(tmp_path):
    from dmux.metrics import render_metrics

    task = {"directory": str(tmp_path), "metrics": [metric(label=f"Metric {i}") for i in range(5)]}
    pages = []
    for offset in range(4):
        stream = io.StringIO()
        Console(file=stream, width=100, color_system=None).print(render_metrics(task, MetricReader(), limit=2, offset=offset))
        pages.append(stream.getvalue())
    assert "Metric 0" in pages[0] and "Metric 2" in pages[1] and "Metric 4" in pages[2]
    assert pages[0] == pages[3]


def test_metrics_read_only_inside_selected_experiment_and_never_affect_progress(tmp_path, monkeypatch):
    demo(["--quick", "--project-root", str(tmp_path), "--steps", "5"])
    monitor = Monitor(tmp_path / "monitor", processes=lambda: [], gpu=lambda: {"devices": []})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    reads = []
    original = MetricReader._source
    monkeypatch.setattr(MetricReader, "_source", lambda self, path, *args: reads.append(path) or original(self, path, *args))
    snapshot = monitor.snapshot()
    render_dashboard(snapshot)
    assert reads == []
    stream = io.StringIO()
    Console(file=stream, width=100, color_system=None).print(render_detail(
        snapshot, "tiny_llm", presentation=monitor.adapter.presentation, width=100, height=40))
    text = stream.getvalue()
    assert "Loss" in text and "Perplexity" in text and "best/window" in text
    assert all(path.parent.name == "tiny_llm" for path in reads)
    assert snapshot["saved"] == 15 and all(t["state"] == "complete" for t in snapshot["tasks"])
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("height", [24, 32, 40])
def test_minimal_result_panels_fit_and_single_value_never_gets_fake_curve(tmp_path, height):
    demo(["--quick", "--project-root", str(tmp_path), "--steps", "3"])
    monitor = Monitor(tmp_path / "monitor", processes=lambda: [], gpu=lambda: {"devices": []})
    snapshot = monitor.snapshot()
    for name in ("tiny_llm", "audio"):
        stream = io.StringIO()
        Console(file=stream, width=80, color_system=None).print(render_detail(
            snapshot, name, presentation=monitor.adapter.presentation, width=80, height=height))
        text = stream.getvalue()
        assert len(text.splitlines()) <= height
        if name == "audio":
            assert "Latest value only" in text and "100.0%" in text
            assert not any(block in text for block in "▁▂▃▄▅▆▇█")
