"""Bounded previews and presentation regressions from the live review."""
import csv
import io
import json

import pytest

from rich.console import Console
from rich.text import Text

from dmux.appearance import Appearance
from dmux.comparison import ComparisonView, compare, report
from dmux.experiment_view import PreviewReader, output_previews
from dmux.settings import Settings
from dmux.mouse import help_hint, MouseMap, MouseEvent


def test_light_palette_does_not_paint_background_or_leave_unicode_state_dot(tmp_path):
    settings = Settings(tmp_path / 'settings.json', environ={}, overrides={'theme': 'light', 'box_style': 'ascii'})
    console = Console(width=100)
    segments = list(console.render(Appearance(Text('● RUNNING\nplain text'), settings)))
    assert all(segment.style is None or segment.style.bgcolor is None for segment in segments)
    assert '* RUNNING' in ''.join(segment.text for segment in segments)


def test_json_heads_are_bounded_cached_and_distinct_from_log_tails(tmp_path, monkeypatch):
    path = tmp_path / 'metrics.json'
    path.write_text(json.dumps({'accuracy': .9, 'samples': list(range(5000))}, indent=2))
    log = tmp_path / 'train.jsonl'
    log.write_text('\n'.join(str(i) for i in range(50)))
    from dmux.experiment_view import head_text
    calls = []
    def head(path, **options):
        calls.append(options['max_bytes'])
        return head_text(path, **options)
    monkeypatch.setattr('dmux.experiment_view.head_text', head)
    with pytest.raises(ValueError, match="positive byte limit"):
        head_text(path, max_bytes=-1)
    reader = PreviewReader()
    task = {'directory': str(tmp_path), 'outputs': ['metrics.json', 'train.jsonl']}
    first = output_previews(task, reader)
    assert '"accuracy": 0.9' in '\n'.join(first[0][2])
    assert first[1][2][-1] == '49'
    assert reader.tail(path, n=2)[-1] == '}'
    assert output_previews(task, reader) == first and calls == [4096]
    path.write_text('{"accuracy": 0.8}')
    assert '0.8' in output_previews(task, reader)[0][2][0] and calls == [4096, 4096]


def test_default_value_removes_only_edited_override(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{"theme":"light","interval":9}')
    a = Settings(path, environ={})
    b = Settings(path, environ={})
    a.set('theme', 'cyan-dark')
    b.set('interval', 10)
    b.save()
    a.save()
    assert json.loads(path.read_text()) == {'interval': 10}
    assert a.sources['theme'] == 'default'
    assert a.values['interval'] == 10


def test_unconfigured_comparison_explains_plan_and_report_without_reading_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr('dmux.metrics.MetricReader.read', lambda *a: (_ for _ in ()).throw(AssertionError('metric read')))
    view = ComparisonView({'tasks': [{'stage': 'train', 'metrics': [{'label': 'Loss'}]}]}, {})
    output = io.StringIO()
    Console(file=output, width=100).print(view.render())
    text = output.getvalue()
    assert 'comparison' in text and 'plan.json' in text and 'Loss' in text
    assert 'dmux report' in text and '--metric' in text
    assert 'WINDOW BEST' not in text and not view.worker.pending


def test_reports_omit_unconfigured_metrics_but_retain_errors_and_ambiguity(tmp_path):
    (tmp_path / 'bad.json').write_text('{broken')
    source = {'label': 'Loss', 'type': 'json', 'path': 'bad.json', 'field': 'loss'}
    task = {'experiment': 'bad-read', 'stage': 'train', 'directory': str(tmp_path),
            'state': 'queued', 'metrics': [source]}
    snapshot = {'experiments': [{'tag': name} for name in ('bad-read', 'ambiguous', 'absent')],
                'tasks': [task, {**task, 'experiment': 'ambiguous', 'metrics': [source, source]}]}
    rows = compare(snapshot, {'metric': 'Loss'})
    markdown = report(rows)
    assert '| absent |' not in markdown and 'Omitted 1 experiment' in markdown
    assert '| bad-read |' in markdown and '| ambiguous |' in markdown
    exported = list(csv.DictReader(io.StringIO(report(rows, 'csv'))))
    assert {row['experiment'] for row in exported} == {'bad-read', 'ambiguous'}
    assert all(row['warning'] for row in exported)


def test_wide_footer_restores_navigation_and_keeps_compact_width():
    for view, labels in [('home', ('/ search', 'f filter', 'Enter details')),
                         ('dashboard', ('n/p tabs', 'Enter details', 't tmux', 'l logs'))]:
        full = help_hint(view, width=140)
        assert all(label in full.plain for label in labels)
        assert full.cell_len <= 140
        assert 'k/K' not in full.plain
        narrow = help_hint(view, width=80)
        assert '? help' in narrow.plain and 'o options' in narrow.plain
        assert narrow.cell_len <= 80
    screen = MouseMap()
    console = Console(file=io.StringIO(), width=140, height=40)
    console.print(screen.frame(help_hint('dashboard', width=140)))
    for key in ('n', 'p', '\r', 't', 'l'):
        y, x, _, _ = next(region for region in screen.regions if region[3] == ('key', key))
        assert screen.action(MouseEvent(0, x, y), console.size) == ('key', key)
