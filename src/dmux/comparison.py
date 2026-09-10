"""Explicit result comparison and bounded Markdown/CSV exports."""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from .bindings import render_help
from .mouse import help_hint
from .terminal import UP, DOWN

from .metrics import MetricReader
from .resources import summarize
from .refresh import BackgroundRefresh

COLUMNS = ('experiment', 'stage', 'state', 'metric', 'latest', 'window_best', 'samples', 'unit',
           'cpu_percent', 'rss_bytes', 'gpu_bytes', 'warning')


def validate_config(config):
    if (not isinstance(config, dict) or set(config) - {'metric', 'stage', 'sort', 'descending'}
            or not isinstance(config.get('metric'), str) or not config['metric']):
        raise ValueError('comparison requires a metric label and optional stage, sort, descending')
    if 'stage' in config and (not isinstance(config['stage'], str) or not config['stage']):
        raise ValueError('comparison.stage must be a non-empty string')
    if config.get('sort', 'experiment') not in ('experiment', 'latest', 'window_best'):
        raise ValueError('comparison.sort must be experiment, latest or window_best')
    if 'descending' in config and type(config['descending']) is not bool:
        raise ValueError('comparison.descending must be boolean')


def compare(snapshot, config, reader=None):
    validate_config(config)
    reader = reader or MetricReader()
    metric = config.get('metric')
    if not isinstance(metric, str) or not metric:
        raise ValueError('Choose comparison.metric in the plan or report --metric LABEL')
    experiments = snapshot.get('experiments', [])
    if len(experiments) > 128:
        raise ValueError('Comparison is bounded to 128 experiments; use a narrower plan')
    rows = []
    for experiment in experiments:
        candidates = [(task, source) for task in snapshot['tasks'] if task['experiment'] == experiment['tag']
                      and (not config.get('stage') or task['stage'] == config['stage'])
                      for source in task.get('metrics', []) if source['label'] == metric]
        row = dict.fromkeys(COLUMNS)
        row.update(experiment=experiment['tag'], metric=metric)
        if len(candidates) != 1:
            row['warning'] = 'Metric missing or ambiguous; choose one explicit stage and label'
        else:
            task, source = candidates[0]
            result = reader.read(task['directory'], source)
            values = [point[1] for point in result['points']]
            best = min(values) if values and source.get('goal') == 'min' else max(values) if values and source.get('goal') == 'max' else None
            row.update(stage=task['stage'], state=task['state'], latest=result['latest'], window_best=best,
                       samples=len(values), unit=source.get('unit', ''),
                       warning='; '.join(filter(None, [result.get('error'), *result.get('warnings', [])])))
            resources = summarize(task, snapshot.get('gpu', {}))
            row.update({key: resources[key] for key in ('cpu_percent', 'rss_bytes', 'gpu_bytes')})
        rows.append(row)
    key = config.get('sort', 'experiment')
    if key not in ('experiment', 'latest', 'window_best'):
        raise ValueError('comparison.sort must be experiment, latest or window_best')
    if key != 'experiment' and len({row['unit'] for row in rows if row['latest'] is not None}) > 1:
        raise ValueError('Cannot sort metrics with different units; select comparable sources')
    known = sorted((r for r in rows if r[key] is not None), key=lambda r: r[key], reverse=bool(config.get('descending')))
    return known + [r for r in rows if r[key] is None]


def report(rows, format='markdown'):
    if format == 'csv':
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
    def cell(value):
        return str(value if value is not None else '—').replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['# DMUX result comparison', '',
        'Best means the best valid sample in the bounded recent window, not an all-time best.', '',
        '| ' + ' | '.join(COLUMNS) + ' |', '| ' + ' | '.join('---' for _ in COLUMNS) + ' |',
        *('| ' + ' | '.join(cell(row[key]) for key in COLUMNS) + ' |' for row in rows), ''])




class ComparisonView:
    def __init__(self, snapshot, config, *, window=256):
        self.snapshot, self.config = snapshot, dict(config)
        self.help = False
        self.reader = MetricReader()
        self.reader.max_points = window
        self.rows, self.error, self.offset = [], None, 0
        self.worker = BackgroundRefresh(lambda: compare(self.snapshot, self.config, self.reader))
        self.worker.request()

    def mouse(self, action):
        if not action or getattr(self, 'searching', False):
            return False
        if action[0] == 'close_help':
            self.help = False
            return False
        if self.help and action in (('key', UP), ('key', DOWN)):
            return False
        if action[0] == 'key':
            self.help = False
            return self.key(action[1])
        return False

    def key(self, key):
        if self.help:
            if key in ('?', 'q', '\x1b'):
                self.help = False
            return False
        if key == '?' and not getattr(self, 'searching', False):
            self.help = True
            return False
        from .terminal import UP, DOWN
        if key in ('q', '\x1b', 'c'):
            return True
        if key == 's' and not self.worker.pending:
            names = ('experiment', 'latest', 'window_best')
            current = self.config.get('sort', 'experiment')
            self.config['sort'] = names[(names.index(current) + 1) % len(names)]
            self.worker.request()
        elif key == 'r':
            self.worker.request()
        elif key in ('j', DOWN, 'k', UP):
            self.offset = max(0, self.offset + (1 if key in ('j', DOWN) else -1))
        return False

    def poll(self):
        result = self.worker.take()
        if result is None:
            return False
        rows, error = result
        self.rows, self.error = rows or [], str(error) if error else None
        return True

    def render(self, *, height=40):
        if self.help:
            return render_help("comparison")
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        table = Table('EXPERIMENT', 'STATE', 'LATEST', 'WINDOW BEST', 'SAMPLES', 'UNIT', 'WARNING', expand=True)
        limit = max(1, height - 8)
        self.offset = min(self.offset, max(0, len(self.rows) - limit))
        for row in self.rows[self.offset:self.offset + limit]:
            table.add_row(*(Text(str(row[key] if row[key] is not None else '—'), no_wrap=True, overflow='ellipsis')
                            for key in ('experiment', 'state', 'latest', 'window_best', 'samples', 'unit', 'warning')))
        return Panel(Group(table, Text(self.error or ('Loading bounded metrics…' if self.worker.pending else
                     'Recent-window best only; missing values remain unknown.')),
                     help_hint()),
                     title=Text('DMUX / COMPARISON / ' + str(self.config.get('metric', 'not configured'))), border_style='cyan')


def main(argv=None):
    from .registry import load_adapter
    from .monitor import Monitor
    from .system import HostSampler
    from .settings import Settings
    parser = argparse.ArgumentParser(prog='dmux report', description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--plan-dir', type=Path)
    parser.add_argument('--adapter', default='filesystem')
    parser.add_argument('--metric')
    parser.add_argument('--stage')
    parser.add_argument('--sort', choices=('experiment', 'latest', 'window_best'))
    parser.add_argument('--descending', action='store_true', default=None)
    parser.add_argument('--format', choices=('markdown', 'csv'), default='markdown')
    parser.add_argument('--output', type=Path, help='Create a new report; never overwrite an existing file')
    args = parser.parse_args(argv)
    try:
        adapter, sampler = load_adapter(args.adapter), HostSampler()
        monitor = Monitor(args.plan_dir or adapter.default_queue(args.project_root), project_root=args.project_root,
                          adapter=adapter, processes=lambda: sampler.processes(adapter), gpu=lambda: {'devices': [], 'error': 'not sampled by report'})
        snapshot = monitor.snapshot()
        if snapshot['state'] in ('waiting', 'invalid'):
            raise ValueError(snapshot.get('message', 'Plan unavailable'))
        config = {**snapshot.get('comparison', {}), **{key: getattr(args, key) for key in ('metric', 'stage', 'sort', 'descending')
                                                       if getattr(args, key) is not None}}
        reader = MetricReader()
        reader.max_points = Settings().values['metric_window']
        output = report(compare(snapshot, config, reader), args.format)
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(output)
        else:
            print(output, end='')
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
