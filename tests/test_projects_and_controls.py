import io
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest
from rich.console import Console

from dmux.adapters.filesystem import FilesystemAdapter
from dmux.experiment_view import output_previews, render_detail
from dmux.monitor import Monitor
from dmux.process_actions import ProcessActionError, prepare_stop, stop


def monitor(queue, **kwargs):
    return Monitor(queue, adapter=FilesystemAdapter(), gpu=lambda: {"devices": [], "error": None}, **kwargs)


def test_multiple_projects_can_point_to_separate_results_and_preserve_metadata(tmp_path, monkeypatch):
    queue = tmp_path / "dashboard"
    queue.mkdir()
    projects = {name: {"root": str(tmp_path / name / "code"),
                       "results_dir": str(tmp_path / "storage" / name),
                       "metadata": {"team": name}, "tmux_session": name}
                for name in ("vision", "language")}
    tasks = []
    for name, settings in projects.items():
        directory = Path(settings["results_dir"]) / "run-1"
        directory.mkdir(parents=True)
        (directory / "metrics.json").write_text('{"current": 2, "total": 2}')
        tasks.append({"project": name, "run": "model", "stage": "train", "directory": "run-1",
                      "progress": {"type": "json", "path": "metrics.json"},
                      "outputs": ["metrics.json"], "metadata": {"seed": 7}})
    (queue / "plan.json").write_text(json.dumps({"projects": projects, "tasks": tasks}))
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.chdir(tmp_path)
    snapshot = monitor(queue).snapshot()
    assert snapshot["state"] == "complete"
    assert [m["tag"] for m in snapshot["models"]] == ["vision/model", "language/model"]
    for task in snapshot["tasks"]:
        assert task["directory"] == str(Path(projects[task["project"]]["results_dir"]) / "run-1")
        assert task["metadata"] == {"team": task["project"], "seed": 7}
        assert snapshot["tmux_config"]["links"][task["model"]] == task["project"]
        assert '"current": 2' in "\n".join(output_previews(task)[0][2])
    assert originals == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_cli_results_override_reads_outputs_outside_project(tmp_path, capsys):
    from dmux.cli import main

    queue = tmp_path / "project" / "monitor"
    queue.mkdir(parents=True)
    results = tmp_path / "outputs"
    results.mkdir()
    (results / "DONE").touch()
    (queue / "plan.json").write_text(json.dumps({"tasks": [{"run": "test", "stage": "export",
        "directory": ".", "completion": {"type": "file", "path": "DONE"}}]}))
    main(["json", "--project-root", str(queue.parent), "--queue", "monitor",
          "--results-dir", str(results), "--no-gpu"])
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["state"] == "complete"
    assert snapshot["tasks"][0]["directory"] == str(results)


@pytest.fixture
def live_experiment(tmp_path):
    script = tmp_path / "dmux_test_worker.py"
    script.write_text("import time\ntime.sleep(90)\n")
    queue = tmp_path / "monitor"
    queue.mkdir()
    outputs = [tmp_path / "selected", tmp_path / "unrelated"]
    processes = [subprocess.Popen([sys.executable, str(script), "--out", str(output)]) for output in outputs]
    (queue / "plan.json").write_text(json.dumps({"tasks": [
        {"run": name, "stage": "train", "directory": str(output),
         "process": {"script": script.name}, "metadata": {"seed": 7}}
        for name, output in zip(("selected", "unrelated"), outputs)]}))
    try:
        yield monitor(queue), processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_stop_requires_confirmation_and_verifies_identity_and_only_selected_stage(live_experiment):
    mon, processes = live_experiment
    snapshot = mon.snapshot()
    request = prepare_stop(snapshot, "selected", "train")
    assert request.targets[0].pid == processes[0].pid
    with pytest.raises(ProcessActionError, match="Confirmation"):
        stop(request, confirmation="yes")
    assert all(process.poll() is None for process in processes)
    stale = replace(request, roots=(replace(request.roots[0], started=0),))
    with pytest.raises(ProcessActionError, match="changed"):
        stop(stale, confirmation=request.label)
    assert all(process.poll() is None for process in processes)
    message = stop(request, confirmation="selected/train")
    assert "SIGTERM requested" in message
    processes[0].wait(timeout=5)
    assert processes[1].poll() is None


def test_enter_detail_contains_selected_pid_metadata_and_outputs(live_experiment):
    mon, processes = live_experiment
    snapshot = mon.snapshot()
    output = io.StringIO()
    Console(file=output, width=100, color_system=None).print(render_detail(
        snapshot, "selected", presentation=mon.adapter.presentation, height=40))
    text = output.getvalue()
    assert str(processes[0].pid) in text and str(processes[1].pid) not in text
    assert "seed: 7" in text and "Outputs" in text
    assert all(process.poll() is None for process in processes)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY terminal check")
def test_enter_detail_cancel_stop_and_quit_restore_terminal_and_keep_jobs(live_experiment):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    mon, processes = live_experiment
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    previous = termios.tcgetattr(slave)
    env = dict(os.environ, TERM="xterm-256color",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    cli = subprocess.Popen([sys.executable, "-m", "dmux", "watch", "--queue", str(mon.queue),
                            "--model", "selected", "--no-gpu", "--interval", "0.25"],
                           stdin=slave, stdout=slave, stderr=slave, env=env)
    output = bytearray()

    def until(needle):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                output.extend(os.read(master, 65536))
            if needle in output:
                return
        pytest.fail(f"Missing {needle!r}: {output[-3000:]!r}")

    try:
        until(b"Enter details")
        os.write(master, b"\r")
        until(b"Metadata")
        until(str(processes[0].pid).encode())
        os.write(master, b"k")
        until(b"Stop selected/train?")
        os.write(master, b"\x1b")
        time.sleep(.2)  # allow the isolated Escape sequence to be decoded
        os.write(master, b"qq")
        until(b"Monitor closed")
        assert cli.wait(timeout=3) == 0
        assert termios.tcgetattr(slave) == previous
        assert all(process.poll() is None for process in processes)
    finally:
        if cli.poll() is None:
            cli.terminate()
            cli.wait(timeout=3)
        os.close(master)
        os.close(slave)


def test_kill_command_scopes_to_experiment_and_requires_exact_confirmation(live_experiment, capsys):
    from dmux.cli import main

    mon, processes = live_experiment
    with pytest.raises(SystemExit) as error:
        main(["kill", "--queue", str(mon.queue), "--model", "selected", "--confirm", "wrong"])
    assert error.value.code == 2
    assert all(process.poll() is None for process in processes)
    main(["kill", "--queue", str(mon.queue), "--model", "selected", "--confirm", "selected"])
    processes[0].wait(timeout=5)
    assert "SIGTERM requested" in capsys.readouterr().out
    assert processes[1].poll() is None


def test_stage_with_multiple_matching_workers_stops_each_worker_only(live_experiment):
    mon, processes = live_experiment
    # Distributed workers may share the same output path and stage definition.
    sibling = subprocess.Popen(processes[0].args)
    try:
        snapshot = mon.snapshot()
        selected = next(task for task in snapshot["tasks"] if task["model"] == "selected")
        assert {p["pid"] for p in selected["processes"]} == {processes[0].pid, sibling.pid}
        request = prepare_stop(snapshot, "selected", "train")
        assert {target.pid for target in request.targets} == {processes[0].pid, sibling.pid}
        stop(request, confirmation=request.label)
        processes[0].wait(timeout=5)
        sibling.wait(timeout=5)
        assert processes[1].poll() is None
    finally:
        if sibling.poll() is None:
            sibling.terminate()
            sibling.wait(timeout=5)
