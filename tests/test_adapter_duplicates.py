from importlib.metadata import EntryPoint

import pytest

from dmux.registry import load_adapter


def test_identical_editable_and_distribution_entry_points_load_once(monkeypatch):
    entries = [EntryPoint(name='mlflow', value='dmux_mlflow:MLflowAdapter', group='dmux.adapters') for _ in range(2)]
    monkeypatch.setattr('dmux.registry.entry_points', lambda **kwargs: entries)
    assert load_adapter('mlflow').__class__.__name__ == 'MLflowAdapter'


def test_conflicting_entry_points_fail_before_loading(monkeypatch):
    entries = [EntryPoint(name='mlflow', value=value, group='dmux.adapters')
               for value in ('dmux_mlflow:MLflowAdapter', 'other:Adapter')]
    monkeypatch.setattr('dmux.registry.entry_points', lambda **kwargs: entries)
    with pytest.raises(ValueError, match='Multiple entry points'):
        load_adapter('mlflow')
