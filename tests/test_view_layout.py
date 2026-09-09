"""Content-driven layouts preserve useful detail across terminal sizes."""
import io
import json

import pytest
from rich.console import Console

from dmux.adapters.base import Presentation
from dmux.demo import main as demo
from dmux.experiment_view import render_detail
from dmux.monitor import Monitor
from dmux.ui import experiment_tabs, render_dashboard


def rendered(view, width):
    output = io.StringIO()
    Console(file=output, width=width, color_system=None).print(view)
    return output.getvalue()


@pytest.fixture
def demo_snapshot(tmp_path, capsys):
    root = tmp_path / ("long-results-directory-" * 5)
    demo(["--quick", "--project-root", str(root), "--steps", "3"])
    capsys.readouterr()
    monitor = Monitor(root / "monitor", processes=lambda: [], gpu=lambda: {"devices": [], "error": "disabled"})
    return monitor, monitor.snapshot()


@pytest.mark.parametrize("width,height", [(80, 42), (120, 42), (140, 60)])
def test_detail_uses_available_rows_for_outputs_and_log(demo_snapshot, width, height):
    monitor, snapshot = demo_snapshot
    text = rendered(render_detail(snapshot, "tiny_llm", presentation=monitor.adapter.presentation,
                                  width=width, height=height), width)
    assert "Outputs" in text and "Recent log" in text
    assert "progress.json" in text and "history.jsonl" in text
    assert "long-results-directory" not in text.split("Outputs", 1)[1]
    assert "Loss" in text and "Perplexity" in text
    assert len(text.splitlines()) <= height
    assert text.count("? help") == 1


def test_dashboard_log_uses_available_rows_at_42(demo_snapshot):
    monitor, snapshot = demo_snapshot
    snapshot["recent"] = ["epoch 3 completed", "checkpoint saved"]
    text = rendered(render_dashboard(snapshot, selected="tiny_llm", width=120, height=42), 120)
    assert "Recent activity" in text and "checkpoint saved" in text
    assert len(text.splitlines()) <= 42


def test_tab_names_expand_to_available_columns(demo_snapshot):
    monitor, snapshot = demo_snapshot
    tabs = experiment_tabs(snapshot["experiments"], "tiny_clip", monitor.adapter.presentation, 140)
    for experiment in snapshot["experiments"]:
        assert monitor.adapter.presentation.entity_name(experiment["tag"]) in tabs.plain
    assert tabs.cell_len <= 140


@pytest.mark.parametrize("width", [40, 80, 140])
def test_tabs_page_and_fit_wide_character_names(width):
    names = {str(i): "実験 " * 12 + str(i) for i in range(12)}
    presentation = Presentation(name="test", stages=("run",), labels={}, short_labels=("Run",), entity_names=names)
    tabs = experiment_tabs([{"tag": tag} for tag in names], "7", presentation, width)
    assert tabs.cell_len <= width
    assert any(span.style == "bold black on bright_cyan" for span in tabs.spans)


def test_compact_dashboard_footer_does_not_wrap(demo_snapshot):
    _, snapshot = demo_snapshot
    text = rendered(render_dashboard(snapshot, width=80, height=42), 80)
    assert "? help" in text.splitlines()[-1]
    assert "n/p" in text.splitlines()[-1]
    assert "read-only" in text.lower()
    snapshot.update(models=[], experiments=[], hidden_count=4)
    text = rendered(render_dashboard(snapshot, width=80, height=42), 80)
    assert "u restore" in text.splitlines()[-1]


def test_gpu_disabled_is_distinct_from_failed(demo_snapshot):
    _, snapshot = demo_snapshot
    text = rendered(render_dashboard(snapshot, width=120, height=42), 120)
    assert "GPU disabled" in text and "GPU unavailable" not in text
    snapshot["gpu"] = {"devices": [], "error": "nvidia-smi timed out after 2s"}
    text = rendered(render_dashboard(snapshot, width=120, height=42), 120)
    assert "timed out after 2s" in text


def test_monitor_retains_gpu_reading_on_error_and_clears_on_disable(tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": []}))
    devices = [{"name": "test", "utilization": 15, "used_gib": 1, "total_gib": 4}]
    readings = iter([{"devices": devices, "error": None},
                     {"devices": [], "error": "nvidia-smi timed out after 2s"},
                     {"devices": [], "error": "disabled"},
                     {"devices": devices, "error": None}])
    monitor = Monitor(tmp_path, processes=lambda: [], gpu=lambda: next(readings))
    assert monitor.snapshot(now=0)["gpu"]["devices"] == devices
    stale = monitor.snapshot(now=5)
    assert stale["gpu"]["devices"] == devices and stale["gpu"]["stale"]
    text = rendered(render_dashboard(stale, width=120, height=42), 120)
    assert "last good reading" in text and "timed out after 2s" in text
    disabled = monitor.snapshot(now=10)["gpu"]
    assert not disabled["devices"] and not disabled.get("stale")
    assert not monitor.snapshot(now=15)["gpu"].get("stale")


@pytest.mark.parametrize("height", [24, 42])
def test_dense_details_reserve_results_integrity_and_controls(demo_snapshot, height):
    monitor, snapshot = demo_snapshot
    task = next(task for task in snapshot["tasks"] if task["model"] == "tiny_llm")
    snapshot["tasks"] += [{**task, "name": f"extra-{i}", "label": "Another configured pipeline stage", "state": "queued"}
                          for i in range(8)]
    snapshot["warnings"] = ["Some output records need review.", "A configured source is temporarily unreadable."]
    text = rendered(render_detail(snapshot, "tiny_llm", presentation=monitor.adapter.presentation,
                                  width=80, height=height, notice="Refresh completed"), 80)
    assert len(text.splitlines()) <= height
    assert "Some output records need review." in text
    assert "3 / 3 saved" in text and "Loss" in text and "? help" in text
    assert "Refresh completed" in text


def test_home_singular_header_and_navigation_first_help(tmp_path):
    from dmux.bindings import render_help
    from dmux.catalog import ProjectCatalog
    from dmux.home import Home
    (tmp_path / "plan.json").write_text(json.dumps({"tasks": []}))
    catalog = ProjectCatalog()
    catalog.add(tmp_path)
    home = Home(catalog)
    home.entries = catalog.read()
    text = rendered(home.render(height=42), 120)
    assert "1 project ·" in text and "1 projects" not in text
    text = rendered(render_help("home"), 100)
    assert text.index("Next run") < text.index("Refresh") < text.index("Show keyboard help")


@pytest.mark.parametrize("background", [False, True])
def test_host_gpu_cache_retains_reading_on_timeout(monkeypatch, background):
    from dmux.system import HostSampler
    devices = [{"name": "test", "utilization": 15, "used_gib": 1, "total_gib": 4}]
    sampler = HostSampler(background=background)
    sampler.devices = {"devices": devices, "error": None}
    monkeypatch.setattr("dmux.system.gpu_info", lambda: {"devices": [], "error": "nvidia-smi timed out after 2s"})
    if background:
        sampler.gpu_time = float("inf")
        sampler.gpu_worker.results.put((None, TimeoutError("nvidia-smi timed out after 2s")))
    data = sampler.gpu()
    assert data["devices"] == devices and data["stale"]
    assert "timed out" in data["error"]


def test_gpu_timeout_message_names_the_command(monkeypatch):
    import subprocess
    from dmux.system import gpu_info
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("nvidia-smi", 2)
    monkeypatch.setattr("dmux.system.subprocess.run", timeout)
    assert gpu_info() == {"devices": [], "error": "nvidia-smi timed out after 2s"}
