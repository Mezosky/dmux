"""Validated user preferences; never experiment-control policy."""
from __future__ import annotations

import argparse
from copy import deepcopy
from importlib.resources import files
import json
import os
from pathlib import Path

from .catalog import ProjectCatalog, atomic_json, user_directory
from .connectors import open_regular

DEFAULTS = {
    'interval': 2.0, 'gpu': True, 'gpu_interval': 5.0,
    'theme': 'cyan-dark', 'box_style': 'unicode', 'key_style': 'both',
    'start_view': 'home', 'log_tail': 500, 'metric_window': 256,
    'clock': 'local', 'hide_completed_after': 0, 'notify_cmd': [],
    'tmux_socket': '', 'ai_cli': [], 'banner': True,
    'stall_seconds': 0, 'history': False,
}


def validate(values):
    from jsonschema import Draft202012Validator
    schema = json.loads(files('dmux').joinpath('schemas/settings.schema.json').read_text())
    error = next(Draft202012Validator(schema).iter_errors(values), None)
    if error:
        raise ValueError('Invalid settings: ' + error.message)
    # jsonschema numbers may otherwise accept NaN / Infinity from Python APIs.
    json.dumps(values, allow_nan=False)
    for key in ('notify_cmd', 'ai_cli'):
        if any('\x00' in argument for argument in values.get(key, [])):
            raise ValueError(f'{key} arguments cannot contain NUL bytes')
    if isinstance(values.get('theme'), dict):
        from rich.color import Color
        for color in values['theme'].values():
            try:
                Color.parse(color)
            except Exception as exc:
                raise ValueError(f'Invalid theme color: {color}') from exc
    return values


def parse_value(value):
    try:
        return json.loads(value)
    except ValueError:
        return value


class Settings:
    def __init__(self, path=None, *, overrides=None, environ=None):
        self.path = Path(path) if path is not None else user_directory('config') / 'settings.json'
        self.overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
        self.environ = os.environ if environ is None else environ
        self.values, self.sources, self.dirty = {}, {}, {}
        self.reload()

    def read(self):
        if self.path.is_symlink():
            raise ValueError('Refusing settings symlink')
        try:
            with open_regular(self.path) as handle:
                raw = handle.read(65_537)
            if len(raw) > 65_536:
                raise ValueError('Settings exceed 64 KiB')
            return validate(json.loads(raw))
        except FileNotFoundError:
            return {}

    def reload(self):
        stored = self.read()
        values = deepcopy(DEFAULTS)
        self.sources = dict.fromkeys(values, 'default')
        for source, changes in (('file', stored), ('session', self.dirty),
                                ('environment', {key: parse_value(self.environ['DMUX_' + key.upper()])
                                 for key in DEFAULTS if 'DMUX_' + key.upper() in self.environ}),
                                ('CLI', self.overrides)):
            validate(changes)
            values.update(changes)
            self.sources.update(dict.fromkeys(changes, source))
        self.values = values

    def set(self, key, value):
        validate({key: value})
        self.dirty[key] = value
        self.reload()

    def save(self):
        if not self.dirty:
            return
        # Reuse the catalog's bounded file lock; merge only edited keys so
        # another dmux instance's unrelated preferences are preserved.
        with ProjectCatalog(self.path)._locked():
            stored = self.read()
            stored.update(self.dirty)
            validate(stored)
            atomic_json(self.path, stored)
        self.dirty.clear()
        self.reload()

    def reset(self, key=None):
        if key is not None and key not in DEFAULTS:
            raise ValueError('Unknown setting: ' + key)
        with ProjectCatalog(self.path)._locked():
            stored = self.read()
            if key is None:
                stored.clear()
            else:
                stored.pop(key, None)
            atomic_json(self.path, stored)
        self.dirty.clear()
        self.reload()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dmux settings', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    get = commands.add_parser('get')
    get.add_argument('key', choices=tuple(DEFAULTS))
    set_parser = commands.add_parser('set')
    set_parser.add_argument('key', choices=tuple(DEFAULTS))
    set_parser.add_argument('value', help='JSON value, or an unquoted string')
    reset = commands.add_parser('reset')
    reset.add_argument('key', nargs='?', choices=tuple(DEFAULTS))
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        if args.command == 'set':
            settings.set(args.key, parse_value(args.value))
            settings.save()
        elif args.command == 'reset':
            settings.reset(args.key)
        print(json.dumps(settings.values if args.command == 'list' else
                         settings.values.get(getattr(args, 'key', None)), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


def add_options(parser, *, existing=()):
    """Expose runtime preferences consistently without replacing command parsers."""
    import argparse
    for key in DEFAULTS:
        if key in existing or key in ('start_view', 'ai_cli'):
            continue
        if key == 'gpu':
            parser.add_argument('--gpu', action=argparse.BooleanOptionalAction, default=None)
        elif isinstance(DEFAULTS[key], bool):
            parser.add_argument('--' + key.replace('_', '-'), action=argparse.BooleanOptionalAction, default=None)
        else:
            parser.add_argument('--' + key.replace('_', '-'), default=None,
                                help=f'Override {key} setting')


def from_args(args):
    import shlex
    overrides = {}
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = shlex.split(value) if key in ('notify_cmd', 'ai_cli') and isinstance(value, str) else (
                str(value) if isinstance(value, Path) else parse_value(value) if isinstance(value, str) else value)
    if getattr(args, 'no_gpu', None) is not None:
        overrides['gpu'] = not args.no_gpu
    return Settings(overrides=overrides)
