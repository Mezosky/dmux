"""tmux integration uses a private temporary server, never the user's server."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from dmux.tmux import PANE_FORMAT, TmuxNavigator, associate, parse_links, parse_panes, render_picker

PANES = "$1\t@2\t%3\t100\ttrain\t0\t0\t1\t1\tbash\n$2\t@4\t%5\t200\tmonitor\t0\t0\t1\t1\tpython\n"


def test_panes_require_numeric_ids_and_render_names_as_plain_text():
    panes = parse_panes(PANES)
    assert len(panes) == 2 and panes[1]["target"] == "$1:@2.%3"
    assert parse_panes(PANES.replace("%3", "%3;kill-server")) == [panes[0]]
    assert parse_panes("malformed\n") == []


def test_links_are_explicit_and_unique():
    assert parse_links(["classifier=train:0.0", "*=logs"]) == {"classifier":"train:0.0", "*":"logs"}
    for values in [["bad"], ["classifier="], ["classifier=train", "classifier=other"]]:
        with pytest.raises(ValueError):
            parse_links(values)


def test_association_uses_ancestry_not_experiment_name_guessing():
    tasks = [{"model":"classifier", "pid":300}, {"model":"regressor", "pid":None}]
    links = associate(parse_panes(PANES), tasks, {}, parent_lookup=lambda pid:[pid,100,1])
    assert links["classifier"]["pane"]["session"] == "train" and "regressor" not in links
    links = associate(parse_panes(PANES), tasks, {"classifier":"monitor"})
    assert links["classifier"]["source"] == "explicit link"
    links = associate(parse_panes(PANES), tasks, {"classifier":"missing"})
    assert links["classifier"]["pane"] is None


def fake_nav(*, clients="$2\t/dev/pts/7\n", inside=False, panes=PANES, **kwargs):
    calls = []
    def run(cmd, **opts):
        calls.append((cmd, opts))
        output = panes if "list-panes" in cmd else clients if "list-clients" in cmd else ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")
    env = {"TMUX":"/tmp/test-socket,99,2", "TMUX_PANE":"%5"} if inside else {}
    return TmuxNavigator(run=run, env=env, **kwargs), calls


def test_discovery_does_not_switch_or_start_anything():
    nav, calls = fake_nav()
    assert len(nav.snapshot([])["panes"]) == 2
    assert calls[0][0] == ["tmux", "-N", "list-panes", "-a", "-F", PANE_FORMAT]


def test_outside_attaches_exact_ids_without_detaching_other_clients():
    nav, calls = fake_nav()
    assert "Returned" in nav.open(parse_panes(PANES)[1])
    command, opts = calls[-1]
    assert command == ["tmux", "-N", "attach-session", "-E", "-t", "$1:@2.%3"]
    assert "capture_output" not in opts and "shell" not in opts


def test_inside_switches_only_the_identified_client():
    nav, calls = fake_nav(inside=True)
    assert "Switched" in nav.open(parse_panes(PANES)[1])
    assert calls[-1][0] == ["tmux", "-N", "switch-client", "-E", "-c", "/dev/pts/7", "-t", "$1:@2.%3"]


def test_multiple_clients_require_explicit_client():
    clients = "$2\t/dev/pts/7\n$2\t/dev/pts/8\n"
    nav, calls = fake_nav(inside=True, clients=clients)
    assert "unique client" in nav.open(parse_panes(PANES)[1])
    assert not any("switch-client" in cmd for cmd, _ in calls)
    nav, calls = fake_nav(inside=True, clients=clients, client="/dev/pts/8")
    assert "Switched" in nav.open(parse_panes(PANES)[1])
    assert "/dev/pts/8" in calls[-1][0]


def test_missing_and_cross_server_targets_never_switch():
    nav, calls = fake_nav(inside=True, panes="")
    assert "disappeared" in nav.open(parse_panes(PANES)[1])
    nav, calls = fake_nav(inside=True, socket="/tmp/different-test-socket")
    assert "Cross-server" in nav.open(parse_panes(PANES)[1])
    assert not any("switch-client" in cmd or "attach-session" in cmd for cmd, _ in calls)


def test_missing_binary_graceful():
    def missing(*args, **kwargs):
        raise FileNotFoundError
    snap = TmuxNavigator(run=missing, env={}).snapshot([])
    assert snap["panes"] == [] and snap["error"] == "tmux is not installed"


@pytest.mark.parametrize("count,index", [(0,0),(2,0),(60,59)])
def test_picker_fits_small_terminal(count,index):
    from rich.console import Console
    panes = (parse_panes(PANES) * 30)[:count]
    output = io.StringIO()
    Console(file=output,width=80,height=24,color_system=None).print(render_picker(
        {"panes":panes,"associations":{},"inside":False},index=index,height=24))
    assert len(output.getvalue().splitlines()) <= 24
    assert "Enter open" in output.getvalue()


@pytest.fixture
def private_tmux(tmp_path):
    if not shutil.which("tmux") or sys.platform != "linux":
        pytest.skip("tmux/Linux PTY integration unavailable")
    # The explicit per-test socket is the only server created or destroyed here.
    socket = tmp_path / "test.sock"
    env = {k:v for k,v in os.environ.items() if k not in {"TMUX","TMUX_PANE"}}
    env["TERM"] = "xterm-256color"
    env["PYTHONPATH"] = str(ROOT / "src")
    prefix = ["tmux", "-S", str(socket)]
    def tmux(*args):
        return subprocess.run([*prefix, *args], env=env, capture_output=True, text=True, timeout=3, check=True).stdout
    tmux("-f", "/dev/null", "new-session", "-d", "-s", "train", "/bin/sh")
    try:
        yield socket, env, tmux
    finally:
        subprocess.run([*prefix, "kill-server"], env=env, capture_output=True, timeout=3, check=False)


@pytest.mark.parametrize("inside", [False, True])
def test_real_attach_or_switch_and_return_restores_terminal(private_tmux,tmp_path,inside):
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time
    socket, env, tmux = private_tmux
    queue = tmp_path / "queue"
    queue.mkdir()
    original = json.dumps({"tasks":[], "experiments":[{"tag":"classifier"}]})
    (queue / "plan.json").write_text(original)
    baseline_pane = tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}").strip()
    command = [sys.executable, "-m", "dmux", "--plan-dir", str(queue),
               "--tmux-socket", str(socket), "--tmux-link", "classifier=train", "--no-gpu", "--color", "always"]
    if inside:
        tmux("new-session", "-d", "-s", "dashboard", *command)
        command = ["tmux", "-S", str(socket), "attach-session", "-t", "dashboard"]
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH",30,100,0,0))
    previous = termios.tcgetattr(slave)
    process = subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,env=env)
    output = bytearray()
    def wait_for(needle):
        start = len(output)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if select.select([master],[],[],.05)[0]:
                output.extend(os.read(master,65536))
            if needle in output[start:]:
                return
        pytest.fail(f"Missing terminal text {needle!r}: {bytes(output[-2000:])!r}")
    try:
        wait_for(b"DMUX")
        os.write(master,b"t")
        wait_for(b"SESSION & PANE PICKER")
        # Explicit link is preselected even when dashboard sorts before train.
        os.write(master,b"\r")
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if "train" in tmux("list-clients", "-F", "#{session_name}").splitlines():
                break
            if select.select([master],[],[],.05)[0]:
                output.extend(os.read(master,65536))
        else:
            pytest.fail(f"Did not attach/switch to train: {bytes(output[-2000:])!r}")
        assert baseline_pane in tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}")
        if inside:
            client = tmux("list-clients", "-F", "#{client_tty}").strip()
            tmux("switch-client", "-c", client, "-t", "dashboard")
            wait_for(b"DMUX")
            os.write(master,b"q")
            # The test dashboard session ends, leaving its client on train.
            time.sleep(.2)
            os.write(master,b"\x02d")
        else:
            os.write(master,b"\x02d")
            wait_for(b"Returned from tmux")
            os.write(master,b"q")
        process.wait(timeout=5)
        assert process.returncode == 0
        assert termios.tcgetattr(slave) == previous
        assert (queue / "plan.json").read_text() == original
        assert sorted(p.name for p in queue.iterdir()) == ["plan.json"]
        assert baseline_pane in tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        os.close(master)
        os.close(slave)
