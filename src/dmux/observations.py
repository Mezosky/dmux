"""Opt-in transition history and notification delivery, separate from liveness."""
from __future__ import annotations

import argparse
import json
import math
from queue import Empty, Full, Queue
import subprocess
from threading import Thread

from .catalog import ProjectCatalog, atomic_json, user_directory
from .connectors import open_regular


class Timeline:
    def __init__(self, path=None):
        self.path = path if path is not None else user_directory('state') / 'timeline.json'

    def read(self):
        if self.path.is_symlink():
            raise ValueError('Refusing timeline symlink')
        try:
            with open_regular(self.path) as handle:
                raw = handle.read(524_289)
            if len(raw) > 524_288:
                raise ValueError('Timeline exceeds 512 KiB')
            data = json.loads(raw)
            if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('events'), list)
                    or len(data['events']) > 2000 or any(not isinstance(event, dict) for event in data['events'])):
                raise ValueError('Invalid timeline version or events')
            for event in data['events']:
                self.validate(event)
            return data['events']
        except FileNotFoundError:
            return []

    @staticmethod
    def validate(event):
        fields = {'observed_at', 'plan_dir', 'experiment', 'stage', 'state', 'previous_state', 'kind'}
        if (not isinstance(event, dict) or set(event) != fields
                or not all(isinstance(event[k], str) for k in ('plan_dir', 'experiment', 'stage', 'state', 'kind'))
                or type(event['observed_at']) not in (int, float) or not math.isfinite(event['observed_at'])
                or not isinstance(event['previous_state'], (str, type(None)))
                or event['kind'] not in ('observed', 'stalled', 'progress_resumed', 'state_changed')):
            raise ValueError('Invalid timeline event')

    def append(self, events):
        if not events:
            return
        for event in events:
            self.validate(event)
        with ProjectCatalog(self.path)._locked():
            combined = (self.read() + events)[-2000:]
            while len(json.dumps({'version': 1, 'events': combined}, indent=2).encode()) > 500_000:
                combined.pop(0)
            atomic_json(self.path, {'version': 1, 'events': combined})


class Notifier:
    def __init__(self):
        self.queue, self.error, self.started = Queue(maxsize=32), None, False

    def send(self, argv, event):
        if not argv:
            return
        try:
            self.queue.put_nowait((tuple(argv), json.dumps(event).encode()))
        except Full:
            self.error = 'Notification queue full; event was not delivered'
            return
        if not self.started:
            self.started = True
            Thread(target=self._work, daemon=True, name='dmux-notifications').start()

    def _work(self):
        while True:
            try:
                argv, payload = self.queue.get(timeout=1)
            except Empty:
                continue
            try:
                result = subprocess.run(argv, input=payload, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=5, check=False)
                if result.returncode:
                    self.error = f'Notification command exited {result.returncode}'
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.error = f'Notification command failed: {exc}'
            finally:
                self.queue.task_done()


class Observations:
    def __init__(self, settings, *, timeline=None, notifier=None):
        self.settings, self.previous, self.progress = settings, {}, {}
        self.timeline = timeline or Timeline()
        self.notifier = notifier or Notifier()

    def observe(self, snapshot):
        if snapshot.get('state') in ('waiting', 'invalid', 'unavailable') and not snapshot.get('tasks'):
            return snapshot
        now = snapshot['updated']
        threshold = self.settings.values['stall_seconds']
        events, advisories = [], []
        for task in snapshot.get('tasks', []):
            key = (snapshot['plan_dir'], task['experiment'], task['stage'])
            progress = task.get('progress')
            saved = progress.get('saved') if progress else None
            old_saved, last_change = self.progress.get(key, (saved, now))
            if saved != old_saved:
                last_change = now
            self.progress[key] = saved, last_change
            age = max(0, now - last_change)
            if progress and progress.get('last_save_age_seconds') is not None:
                age = max(age, progress['last_save_age_seconds'])
            if task.get('process_started') is not None:
                age = min(age, max(0, now - task['process_started']))
            stalled = bool(threshold and progress and task['state'] in ('running', 'scheduler running') and age >= threshold)
            state = task['state'], stalled
            previous = self.previous.get(key)
            if stalled:
                advisories.append(f'{task["experiment"]}/{task["stage"]}: stalled progress ({age:.0f}s without a committed update); process state unchanged')
            if state != previous:
                event = {'observed_at': now, 'plan_dir': key[0][:4096], 'experiment': key[1][:512], 'stage': key[2][:512],
                         'state': state[0], 'previous_state': previous[0] if previous else None,
                         'kind': 'observed' if previous is None else 'stalled' if stalled else
                                 'progress_resumed' if previous[1] and previous[0] == state[0] else 'state_changed'}
                events.append(event)
                if previous is not None:
                    self.notifier.send(self.settings.values['notify_cmd'], event)
            self.previous[key] = state
        # Remove vanished templates from memory without inventing completion events.
        live = {(snapshot['plan_dir'], task['experiment'], task['stage']) for task in snapshot.get('tasks', [])}
        for mapping in (self.previous, self.progress):
            for key in list(mapping):
                if key[0] == snapshot['plan_dir'] and key not in live:
                    del mapping[key]
        if self.settings.values['history']:
            try:
                self.timeline.append(events)
            except (OSError, ValueError) as exc:
                advisories.append(f'Timeline unavailable: {exc}')
        if self.notifier.error:
            advisories.append(self.notifier.error)
        return {**snapshot, 'warnings': [*snapshot.get('warnings', []), *advisories]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dmux timeline', description='Read observed transitions; gaps are not reconstructed')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        events = Timeline().read()
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    if args.json:
        print(json.dumps({'version': 1, 'events': events}, indent=2))
    else:
        from .appearance import timestamp
        from .settings import Settings
        clock = Settings().values['clock']
        for event in events:
            print(f'{timestamp(event["observed_at"], clock)} {event["experiment"]}/{event["stage"]} {event["kind"]}: {event["state"]}')
        if not events:
            print('No observed history. Enable history in settings and run dmux watch or home.')
