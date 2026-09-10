from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.panel import Panel

from dmux.appearance import Appearance, wordmark
from dmux.log_view import LogView
from dmux.observations import Observations, Timeline, Notifier
from dmux.options_view import OptionsView
from dmux.settings import Settings, validate, main
from dmux.terminal import UP, RIGHT


def test_settings_precedence_and_concurrent_key_merge(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{"interval": 3, "theme": "light"}')
    a = Settings(path, environ={'DMUX_INTERVAL': '4'}, overrides={'interval': 5})
    b = Settings(path, environ={})
    assert a.values['interval'] == 5 and a.sources['interval'] == 'CLI'
    a.set('banner', False)
    b.set('log_tail', 42)
    b.save()
    a.save()
    stored = json.loads(path.read_text())
    assert stored == {'interval': 3, 'theme': 'light', 'banner': False, 'log_tail': 42}
    assert a.values['interval'] == 5
    b.reset('theme')
    assert b.values['theme'] == 'cyan-dark'


@pytest.mark.parametrize('value', [{'interval': 0}, {'gpu': 'false'}, {'notify_cmd': 'echo hi'},
                                  {'metric_window': 100000}, {'history': 1}, {'stop_confirmation': False},
                                  {'theme': {}}, {'interval': float('nan')}, {'unknown': True}])
def test_unsafe_or_invalid_settings_rejected(value):
    with pytest.raises(ValueError):
        validate(value)


def test_settings_symlink_and_read_only_cli(tmp_path, capsys):
    path = tmp_path / 'settings.json'
    settings = Settings(path, environ={})
    assert not path.exists()
    settings.save()
    assert not path.exists()
    main(['list'])
    assert json.loads(capsys.readouterr().out)['banner']
    target = tmp_path / 'target'
    target.write_text('{}')
    path.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        Settings(path)
    assert target.read_text() == '{}'


def test_options_arrows_edit_persist_without_control_policy(tmp_path):
    settings = Settings(tmp_path / 'settings.json', environ={})
    view = OptionsView(settings)
    view.key(UP)  # history, last row
    view.key(RIGHT)
    assert settings.values['history'] is True
    assert view.key('o') is True
    assert json.loads(settings.path.read_text()) == {'history': True}
    stream = io.StringIO()
    Console(file=stream, width=100).print(view.render())
    assert 'typed label' in stream.getvalue()


def test_ascii_branding_and_no_color(tmp_path):
    settings = Settings(tmp_path / 's.json', environ={}, overrides={'box_style': 'ascii'})
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, no_color=True, width=70)
    console.print(Appearance(Panel(wordmark(settings)), settings))
    text = output.getvalue()
    assert 'DMUX' in text and 'your experiments, one terminal' in text
    assert not any(0x2500 <= ord(c) <= 0x259f for c in text)
    assert '\x1b[38;' not in text
    settings.set('banner', False)
    assert wordmark(settings).plain == 'DMUX'


def test_log_window_search_freeze_follow_and_rotation(tmp_path):
    path = tmp_path / 'run.log'
    path.write_text(''.join(f'line {i}\n' for i in range(3000)) + '\x1b[31mERROR\x1b[0m\n')
    viewer = LogView(path, limit=100)
    viewer.refresh()
    assert len(viewer.lines) == 100 and viewer.limited and viewer.lines[-1] == 'ERROR'
    viewer.key('/')
    for key in 'ERROR':
        viewer.key(key)
    viewer.key('\r')
    before = list(viewer.lines)
    path.write_text('new generation\n')
    viewer.refresh()
    assert viewer.lines == before and not viewer.follow
    viewer.key('f')
    viewer.refresh()
    assert viewer.lines == ['new generation']
    path.unlink()
    viewer.refresh()
    assert viewer.error and not viewer.lines
    assert viewer.key('q')


def test_log_special_file_does_not_block(tmp_path):
    import os
    fifo = tmp_path / 'log'
    os.mkfifo(fifo)
    view = LogView(fifo)
    view.refresh()
    assert view.error and not view.lines


def snapshot(now=0, *, saved=1, state='running'):
    return {'updated': now, 'plan_dir': '/project/monitor', 'warnings': [], 'tasks': [
        {'experiment': 'run', 'stage': 'train', 'state': state, 'pid': 123,
         'process_started': 0, 'progress': {'saved': saved, 'last_save_age_seconds': 0}}]}


def test_stall_is_advisory_and_notifications_are_transition_only(tmp_path):
    settings = Settings(tmp_path / 's.json', environ={}, overrides={'stall_seconds': 10, 'notify_cmd': ['notify']})
    sent = []
    notifier = SimpleNamespace(error=None, send=lambda argv, event: sent.append((argv, event)))
    observer = Observations(settings, notifier=notifier)
    original = snapshot()
    before = deepcopy(original)
    assert observer.observe(original)['tasks'][0]['state'] == 'running' and not sent
    assert original == before
    stalled = observer.observe(snapshot(11))
    assert stalled['tasks'][0]['state'] == 'running' and 'stalled progress' in stalled['warnings'][0]
    observer.observe(snapshot(12))
    assert len(sent) == 1 and sent[0][1]['kind'] == 'stalled'
    observer.observe(snapshot(13, saved=2))
    observer.observe(snapshot(14, state='complete', saved=2))
    assert [event['kind'] for _, event in sent] == ['stalled', 'progress_resumed', 'state_changed']
    assert not (tmp_path / 'timeline.json').exists()


def test_timeline_is_opt_in_bounded_and_has_no_pids_or_metrics(tmp_path):
    timeline = Timeline(tmp_path / 'timeline.json')
    settings = Settings(tmp_path / 's.json', environ={}, overrides={'history': True})
    observer = Observations(settings, timeline=timeline)
    observer.observe(snapshot())
    observer.observe(snapshot(5, state='complete'))
    events = timeline.read()
    assert len(events) == 2 and events[1]['previous_state'] == 'running'
    assert all('pid' not in row and 'progress' not in row and 'saved' not in row for row in events)
    timeline.append(events * 1100)
    assert len(timeline.read()) <= 2000 and timeline.path.stat().st_size < 524288


def test_notifier_uses_argv_and_bounded_stdin_without_shell(monkeypatch):
    calls = []
    monkeypatch.setattr('dmux.observations.subprocess.run', lambda argv, **kwargs:
                        calls.append((argv, kwargs)) or SimpleNamespace(returncode=0))
    notifier = Notifier()
    notifier.send(['notify', 'literal; $(touch nothing)'], {'state': 'failed'})
    notifier.queue.join()
    argv, kwargs = calls[0]
    assert argv[1] == 'literal; $(touch nothing)' and 'shell' not in kwargs
    assert kwargs['timeout'] == 5 and json.loads(kwargs['input']) == {'state': 'failed'}


def test_options_intercept_stop_keys_and_arrows(tmp_path):
    from dmux.controller import DashboardController
    from dmux.adapters.filesystem import FilesystemAdapter
    settings = Settings(tmp_path / 's.json', environ={})
    controller = DashboardController({'tasks': [], 'models': [], 'experiments': []}, FilesystemAdapter(),
                                     SimpleNamespace(), settings=settings)
    controller.key('o')
    controller.key(UP)
    assert controller.options.index == len(settings.values) - 1 and controller.pending_stop is None
    controller.key('k')
    assert controller.options.index == len(settings.values) - 2 and controller.pending_stop is None
    controller.key('\x1b')
    assert controller.options is None and controller.running


def test_timeline_invalid_event_fails_closed(tmp_path):
    path = tmp_path / 'timeline.json'
    path.write_text('{"version": 1, "events": [{}]}')
    with pytest.raises(ValueError, match='event'):
        Timeline(path).read()
    assert path.read_text() == '{"version": 1, "events": [{}]}'


def test_cli_settings_precedence_does_not_change_explicit_gpu_flag(monkeypatch, tmp_path):
    from dmux.cli import parser
    from dmux.settings import from_args
    monkeypatch.setenv('DMUX_INTERVAL', '9')
    monkeypatch.setenv('DMUX_GPU', 'true')
    settings = from_args(parser().parse_args(['--interval', '3', '--no-gpu']))
    assert settings.values['interval'] == 3 and settings.values['gpu'] is False
    assert from_args(parser().parse_args([])).values['interval'] == 9


def test_custom_theme_color_validation():
    from dmux.appearance import PALETTES
    with pytest.raises(ValueError, match='color'):
        validate({'theme': {**PALETTES['light'], 'accent': 'nonexistent_color'}})


def test_completion_does_not_start_demo(tmp_path, monkeypatch):
    import importlib.util
    if importlib.util.find_spec('argcomplete') is None:
        pytest.skip('optional shell extra absent')
    from dmux.cli import main
    monkeypatch.setenv('_ARGCOMPLETE', '1')
    calls = []
    def finish(parser):
        calls.append(parser)
        raise SystemExit(0)
    monkeypatch.setattr('argcomplete.autocomplete', finish)
    with pytest.raises(SystemExit):
        main(['demo', '--live', '--project-root', str(tmp_path / 'demo')])
    assert calls and not (tmp_path / 'demo').exists()


@pytest.mark.skipif(__import__('sys').platform != 'linux', reason='owned Linux PTY integration')
def test_watch_options_log_and_comparison_restore_terminal(tmp_path):
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import sys
    import termios
    import time
    from pathlib import Path
    (tmp_path / 'log.txt').write_text('training sample\n')
    (tmp_path / 'metric.json').write_text('{"loss": 2}')
    (tmp_path / 'plan.json').write_text(json.dumps({'project_root': str(tmp_path),
        'comparison': {'metric': 'Loss'}, 'tasks': [{'experiment': 'sample', 'directory': '.', 'log': 'log.txt',
        'metrics': [{'label': 'Loss', 'path': 'metric.json', 'field': 'loss', 'type': 'json'}]}]}))
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 120, 0, 0))
    previous = termios.tcgetattr(slave)
    env = {**os.environ, 'TERM': 'xterm-256color',
           'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    child = subprocess.Popen([sys.executable, '-m', 'dmux', 'watch', '--plan-dir', str(tmp_path),
                              '--no-gpu', '--tmux-socket', str(tmp_path / 'absent.sock')],
                             stdin=slave, stdout=slave, stderr=slave, env=env)
    output = bytearray()
    def wait_for(needle):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                output.extend(os.read(master, 65536))
            if needle in output:
                output.clear()
                return
        pytest.fail(repr(bytes(output[-2500:])))
    try:
        wait_for(b'? help')
        os.write(master, b'?')
        wait_for(b'KEYBOARD HELP')
        # Click the actual options help entry through the CLI's mouse decoder
        # and frame map, then continue exercising the keyboard fallback.
        from dmux.bindings import render_help
        from dmux.mouse import MouseMap
        screen = MouseMap()
        Console(file=io.StringIO(), width=120, height=42).print(screen.frame(render_help('dashboard')))
        y, x, _, _ = next(region for region in screen.regions if region[3] == ('key', 'o'))
        os.write(master, f'\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m'.encode())
        wait_for(b'OPTIONS')
        os.write(master, b'o')
        time.sleep(.2)
        os.write(master, b'l')
        wait_for(b'FOLLOW')
        os.write(master, b'q')
        time.sleep(.2)
        os.write(master, b'c')
        wait_for(b'COMPARISON')
        os.write(master, b'q')
        time.sleep(.2)
        os.write(master, b'q')
        child.wait(timeout=5)
        assert child.returncode == 0 and termios.tcgetattr(slave) == previous
        assert (tmp_path / 'log.txt').read_text() == 'training sample\n'
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)
