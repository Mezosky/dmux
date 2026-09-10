"""Keyboard-editable preferences shared by each interactive view."""
import json

from .settings import DEFAULTS, parse_value
from .terminal import UP, DOWN, LEFT, RIGHT

CHOICES = {'theme': ['cyan-dark', 'light', 'high-contrast', 'monochrome'],
           'box_style': ['unicode', 'ascii'], 'key_style': ['both', 'arrows', 'letters'],
           'clock': ['local', 'UTC'], 'start_view': ['home', 'watch']}


class OptionsView:
    def __init__(self, settings):
        self.settings = settings
        self.index, self.editing, self.text, self.notice = 0, False, '', None

    def key(self, key):
        name = list(DEFAULTS)[self.index]
        try:
            if self.editing:
                if key == '\x1b':
                    self.editing = False
                elif key in ('\r', '\n'):
                    self.settings.set(name, parse_value(self.text))
                    self.editing = False
                elif key in ('\b', '\x7f'):
                    self.text = self.text[:-1]
                elif key.isprintable():
                    self.text += key
            elif key in ('q', '\x1b', 'o'):
                self.settings.save()
                return True
            elif key in ('j', DOWN, 'k', UP):
                self.index = (self.index + (1 if key in ('j', DOWN) else -1)) % len(DEFAULTS)
            elif key in ('\r', '\n', LEFT, RIGHT):
                value = self.settings.values[name]
                choices = [False, True] if isinstance(value, bool) else CHOICES.get(name)
                if choices:
                    index = choices.index(value) if value in choices else -1
                    self.settings.set(name, choices[(index + (-1 if key == LEFT else 1)) % len(choices)])
                else:
                    self.text, self.editing = json.dumps(value), True
            self.notice = None
        except (ValueError, OSError) as exc:
            self.notice = str(exc)
        return False

    def render(self, *, height=40):
        from rich.panel import Panel
        from rich.text import Text
        keys = list(DEFAULTS)
        limit = max(1, height - 10)
        start = max(0, min(self.index - limit // 2, len(keys) - limit))
        body = Text()
        for index in range(start, min(start + limit, len(keys))):
            key = keys[index]
            value = self.text + '|' if self.editing and index == self.index else json.dumps(self.settings.values[key])
            body.append(f'{">" if index == self.index else " "} {key:22} {value} [{self.settings.sources[key]}]\n',
                        style='bold cyan' if index == self.index else None)
        body.append('\nj/k select; Enter edit/toggle; arrows change; Esc/o close and save\n')
        body.append('CLI and environment overrides remain authoritative.\n')
        body.append('Preferences never change experiments. Stops always require a typed label.')
        if self.notice:
            body.append('\n! ' + self.notice, style='yellow')
        return Panel(body, title='DMUX / OPTIONS', border_style='cyan')
