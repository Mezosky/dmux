"""Mouse targets follow rendered cells; pointer input never confirms controls."""
import io
import json
import os
import signal
import sys
from types import SimpleNamespace

import pytest
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from dmux.appearance import Appearance, HOME_LOCKUP, wordmark
from dmux.bindings import render_help
from dmux.controller import DashboardController
from dmux.experiment_view import render_detail
from dmux.log_view import LogView
from dmux.monitor import Monitor
from dmux.mouse import MouseEvent, MouseMap, target
from dmux.options_view import OptionsView
from dmux.settings import Settings
from dmux.terminal import UP, keyboard
from dmux.ui import render_dashboard


def draw(view, *, width=100, height=42):
    stream = io.StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    screen = MouseMap()
    console.print(screen.frame(view))
    return screen, console.size, stream.getvalue()


def click(screen, size, action):
    y, x, _, _ = next(region for region in screen.regions if region[3] == action)
    return screen.action(MouseEvent(0, x, y), size)


@pytest.fixture
def controller(tmp_path):
    (tmp_path / 'plan.json').write_text(json.dumps({'tasks': [
        {'experiment': 'one', 'stage': 'train'}, {'experiment': 'two', 'stage': 'train'},
        {'experiment': 'two', 'stage': 'eval'}]}))
    monitor = Monitor(tmp_path, processes=lambda: [], gpu=lambda: {'devices': [], 'error': 'disabled'})
    return DashboardController(monitor.snapshot(), monitor.adapter, None)


@pytest.mark.parametrize('width', [40, 80, 140])
@pytest.mark.parametrize('theme,boxes', [('cyan-dark', 'unicode'), ('light', 'unicode'), ('monochrome', 'ascii')])
def test_hits_follow_rendered_cells_with_wide_characters(tmp_path, width, theme, boxes):
    settings = Settings(tmp_path / 's.json', environ={}, overrides={'theme': theme, 'box_style': boxes})
    view = Panel(Text('実験界  ', style=target('experiment', 'wide')) +
                 Text('next', style=target('experiment', 'next')))
    screen, size, text = draw(Appearance(view, settings), width=width)
    assert '実験界' in text
    assert click(screen, size, ('experiment', 'wide')) == ('experiment', 'wide')
    assert click(screen, size, ('experiment', 'next')) == ('experiment', 'next')
    # 3 double-width characters and 2 spaces put the next target 8 cells later.
    wide = next(region for region in screen.regions if region[3][1] == 'wide')
    following = next(region for region in screen.regions if region[3][1] == 'next')
    assert following[1] - wide[1] == 8
    assert screen.action(MouseEvent(0, following[1], following[0]), (width + 1, size.height)) is None
    assert screen.action(MouseEvent(0, 0, 0), size) is None
    assert screen.action(MouseEvent(0, following[1], following[0], False), size) is None
    assert screen.action(MouseEvent(32, following[1], following[0]), size) is None
    assert screen.action(MouseEvent(2, following[1], following[0]), size) is None


def test_hidden_and_previous_frame_regions_are_not_clickable():
    screen, size, _ = draw(Group(Text('top'), Text('offscreen', style=target('key', 'o'))), height=1)
    assert not screen.regions
    console = Console(file=io.StringIO(), width=40, height=10)
    console.print(screen.frame(Text('options', style=target('key', 'o'))))
    assert screen.regions
    console.print(screen.frame(Text('plain')))
    assert not screen.regions


def test_dashboard_tabs_rows_stage_and_help_clicks(controller, monkeypatch):
    monkeypatch.setattr('dmux.controller.prepare_stop', lambda *a: pytest.fail('mouse opened stop'))
    screen, size, text = draw(render_dashboard(controller.snapshot, width=100, height=42))
    assert all(label in text.splitlines()[-1] for label in ('? help', 'o options', 'q quit'))
    controller.mouse(screen.action(MouseEvent(64, 5, 5), size))
    assert controller.current_model() == 'two' and controller.pending_stop is None
    controller.mouse(click(screen, size, ('experiment', 'two')))
    assert controller.current_model() == 'two' and not controller.detailed
    controller.mouse(click(screen, size, ('open_experiment', 'two')))
    assert controller.detailed
    screen, size, _ = draw(render_detail(controller.snapshot, 'two', presentation=controller.adapter.presentation,
                                       width=100, height=42))
    controller.mouse(click(screen, size, ('stage', 'eval')))
    assert controller.stage == 'eval'
    controller.mouse(click(screen, size, ('key', '?')))
    assert controller.help
    screen, size, _ = draw(render_help('detail', controller.settings))
    assert all(action not in (('key', 'k'), ('key', 'K')) for *_, action in screen.regions)
    controller.mouse(click(screen, size, ('key', 'o')))
    assert controller.options is not None and not controller.help
    controller.options = None
    controller.mouse(('key', 'k'))
    controller.mouse(('key', 'K'))
    controller.pending_stop = SimpleNamespace(label='one/train')
    for action in [('key', '\r'), ('key', 'o'), ('open_experiment', 'two'), ('key', UP)]:
        controller.mouse(action)
    assert controller.pending_stop.label == 'one/train' and controller.stop_confirmation == ''


def test_options_click_changes_value_and_help_can_close(tmp_path):
    settings = Settings(tmp_path / 's.json', environ={})
    view = OptionsView(settings)
    screen, size, _ = draw(view.render(height=42))
    view.mouse(click(screen, size, ('option', 'theme')))
    assert settings.values['theme'] == 'light'
    assert not settings.path.exists()  # Saved only on explicit options close.
    view.mouse(('key', '?'))
    assert view.help
    view.mouse(('close_help', ''))
    assert not view.help
    view.mouse(('option', 'interval'))
    assert view.editing
    view.mouse(('option', 'theme'))
    assert settings.values['theme'] == 'light'
    view.key('\x1b')
    view.key('q')
    assert json.loads(settings.path.read_text())['theme'] == 'light'


def test_wheel_scrolls_log_and_never_changes_search(tmp_path):
    path = tmp_path / 'train.log'
    original = ''.join(f'line {i}\n' for i in range(100))
    path.write_text(original)
    view = LogView(path)
    screen, size, _ = draw(view.render())
    view.mouse(screen.action(MouseEvent(64, 5, 5), size))
    assert view.offset == 1 and not view.follow
    view.mouse(screen.action(MouseEvent(65, 5, 5), size))
    assert view.offset == 0
    view.key('/')
    view.mouse(('key', UP))
    assert view.query == '' and view.searching
    assert path.read_text() == original


def test_home_lockup_and_fallbacks(tmp_path):
    settings = Settings(tmp_path / 's.json', environ={})
    assert wordmark(settings, lockup=True).plain.splitlines()[:3] == list(HOME_LOCKUP)
    assert max(Text(line).cell_len for line in HOME_LOCKUP) == 31
    assert '▀' not in wordmark(settings, lockup=True, compact=True).plain
    settings.set('box_style', 'ascii')
    assert '▀' not in wordmark(settings, lockup=True).plain
    settings.set('banner', False)
    assert wordmark(settings, lockup=True).plain == 'DMUX'


@pytest.mark.skipif(sys.platform != 'linux', reason='owned Linux PTY integration')
def test_mouse_decodes_fragmented_packets_and_restores_mode(monkeypatch):
    import pty
    import termios
    import time
    master, slave = pty.openpty()
    previous = termios.tcgetattr(slave)
    output = io.StringIO()
    output.isatty = lambda: True
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setenv('TERM', 'xterm-256color')
    try:
        with os.fdopen(os.dup(slave)) as stream:
            monkeypatch.setattr(sys, 'stdin', stream)
            enabled = [True]
            with pytest.raises(RuntimeError):
                with keyboard(mouse=lambda: enabled[0]) as read:
                    assert '\x1b[?1006h' in output.getvalue()
                    os.write(master, b'\x1b[<0;')
                    assert read() == []
                    time.sleep(.06)  # Longer than ordinary Esc-key timeout.
                    assert read() == []
                    os.write(master, b'12;7M\x1b[<0;12;7m\x1b[<64;12;7Mk')
                    assert read() == [MouseEvent(0, 11, 6), MouseEvent(0, 11, 6, False), MouseEvent(64, 11, 6), 'k']
                    os.write(master, b'\x1b[M kk')
                    assert read() == []  # Legacy coordinates must not open stop.
                    enabled[0] = False
                    read()
                    assert output.getvalue().endswith('\x1b[?1006l\x1b[?1000l')
                    enabled[0] = True
                    read()
                    monkeypatch.setattr(os, 'kill', lambda pid, sig: None)
                    signal.getsignal(signal.SIGTSTP)(signal.SIGTSTP, None)
                    assert '\x1b[?1000l\x1b[?1049l' in output.getvalue()
                    assert output.getvalue().endswith('\x1b[?1000h\x1b[?1006h')
                    raise RuntimeError('owned exception')
        assert output.getvalue().endswith('\x1b[?1006l\x1b[?1000l')
        assert termios.tcgetattr(slave) == previous
    finally:
        os.close(master)
        os.close(slave)


def test_help_fits_standard_terminal_and_has_clickable_close():
    screen, size, text = draw(render_help('dashboard'), width=80, height=24)
    assert len(text.splitlines()) <= 24
    assert click(screen, size, ('close_help', '')) == ('close_help', '')


def test_home_targets_survive_filtering_and_recent_tab_layout(tmp_path):
    from dmux.catalog import ProjectCatalog
    from dmux.home import Home
    (tmp_path / 'plan.json').write_text(json.dumps({'tasks': [{'experiment': 'one'}, {'experiment': 'two'}]}))
    catalog = ProjectCatalog()
    catalog.add(tmp_path)
    home = Home(catalog, sampler=SimpleNamespace(processes=lambda adapter: [], gpu=lambda: {'devices': []}))
    home.refresh(force=True)
    key = next(row['id'] for row in home.rows() if row['experiment'] == 'two')
    home.opened = [key]
    home.query = 'two'
    screen, size, text = draw(home.render(height=42, width=100))
    assert click(screen, size, ('run', key)) == ('run', key)
    assert click(screen, size, ('recent', key)) == ('recent', key)
    assert HOME_LOCKUP[1] in text and '1 project' in text
    assert not any(action[0] == 'run' and action[1].endswith('/one') for *_, action in screen.regions)


def test_session_mouse_never_confirms_removal():
    from dmux.session_browser import SessionBrowser
    navigator = SimpleNamespace(snapshot=lambda tasks: {'panes': [], 'associations': {}},
                                read=lambda args: ('', None))
    browser = SessionBrowser(navigator)
    browser.removal = SimpleNamespace(name='owned-session')
    for action in [('key', '\r'), ('key', 'd'), ('key', 'o'), ('key', UP), ('pane', '$1:@1.%1')]:
        assert browser.mouse(action) is None
    assert browser.removal.name == 'owned-session' and browser.confirmation == ''
