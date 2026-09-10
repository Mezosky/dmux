import io
import json
from types import SimpleNamespace

import pytest

from dmux.comparison import compare, report, main
from dmux.monitor import Monitor
from dmux.resources import ProcessResources, gpu_processes, summarize


def test_comparison_is_explicit_bounded_and_reports_recent_best(tmp_path, monkeypatch):
    for name in ('one', 'two'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'history.jsonl').write_text('{"step":1,"loss":5}\n{"step":2,"loss":3}\n{"step":2,"loss":1}\n')
    plan = {'project_root': str(tmp_path), 'comparison': {'metric': 'Loss', 'stage': 'train'}, 'tasks': [
        {'experiment': name, 'stage': 'train', 'directory': name,
         'metrics': [{'label': 'Loss', 'path': 'history.jsonl', 'field': 'loss', 'x_field': 'step', 'goal': 'min'}]}
        for name in ('one', 'two')]}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    monitor = Monitor(tmp_path, processes=lambda: [], gpu=lambda: {})
    from dmux.metrics import MetricReader
    original = MetricReader.read
    monkeypatch.setattr(MetricReader, 'read', lambda *args: pytest.fail('implicit metric read'))
    snapshot = monitor.snapshot()
    monkeypatch.setattr(MetricReader, 'read', original)
    rows = compare(snapshot, plan['comparison'])
    assert all(row['latest'] == 3 and row['window_best'] == 3 and row['samples'] == 2 for row in rows)
    assert all(row['warning'] for row in rows)
    assert 'not an all-time best' in report(rows)
    import csv
    assert len(list(csv.DictReader(io.StringIO(report(rows, 'csv'))))) == 2
    existing = tmp_path / 'report.md'
    existing.write_text('user data')
    with pytest.raises(SystemExit):
        main(['--project-root', str(tmp_path), '--plan-dir', str(tmp_path), '--output', str(existing)])
    assert existing.read_text() == 'user data'


def test_missing_metric_is_visible_and_limits_fail():
    snapshot = {'experiments': [{'tag': 'one'}], 'tasks': []}
    assert compare(snapshot, {'metric': 'Loss'})[0]['warning']
    snapshot['experiments'] *= 129
    with pytest.raises(ValueError, match='128'):
        compare(snapshot, {'metric': 'Loss'})


def test_cpu_first_sample_unknown_and_pid_reuse_resets():
    cpu = [1]
    process = SimpleNamespace(cpu_times=lambda: SimpleNamespace(user=cpu[0], system=0),
                              memory_info=lambda: SimpleNamespace(rss=1024))
    sampler = ProcessResources()
    assert sampler.read(process, (10, 1), 1)['cpu_percent'] is None
    cpu[0] = 2
    value = sampler.read(process, (10, 1), 3)
    assert value['cpu_percent'] == 50 and value['rss_bytes'] == 1024
    assert sampler.read(process, (10, 2), 4)['cpu_percent'] is None


def test_gpu_process_rows_skip_na_and_pid_reuse_has_no_attribution(monkeypatch):
    monkeypatch.setattr('dmux.resources.read_command', lambda argv: '12, GPU-a, 32\n13, GPU-b, [N/A]\n')
    data = gpu_processes()
    assert data['processes']['12'][0]['used_bytes'] == 32 * 1024**2 and data['error']
    task = {'processes': [{'pid': 12, 'started': data['observed_at'] + 1}]}
    assert summarize(task, {'process_memory': data})['gpu_bytes'] is None
    task['processes'][0]['started'] -= 2
    assert summarize(task, {'process_memory': data})['gpu_bytes'] == 32 * 1024**2
