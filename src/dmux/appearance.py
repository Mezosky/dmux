"""Terminal presentation tokens and a zero-delay product wordmark."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import unicodedata

PALETTES = {
    'cyan-dark': dict(accent='#22d3ee', ok='#4ade80', warning='#facc15', error='#fb7185', muted='#94a3b8', selected='#0891b2'),
    'light': dict(accent='#0369a1', ok='#166534', warning='#854d0e', error='#b91c1c', muted='#475569', selected='#0369a1'),
    'high-contrast': dict(accent='#00ffff', ok='#00ff00', warning='#ffff00', error='#ff4040', muted='#ffffff', selected='#ffffff'),
    'monochrome': dict.fromkeys(('accent', 'ok', 'warning', 'error', 'muted', 'selected'), '#ffffff'),
}


HOME_LOCKUP = (
    '▀▄        ▄▀',
    '▄█▀▀████▀▀█▄  █▀▄ █▀▄▀█ █ █ ▀▄▀',
    '▀██▀█▀▀█▀██▀  █▄▀ █ ▀ █ █▄█ ▄▀▄',
)


def wordmark(settings=None, *, context='', compact=False, lockup=True, tagline=True):
    from rich.text import Text
    values = settings.values if settings is not None else {'banner': True, 'box_style': 'unicode'}
    if compact or not values['banner']:
        return Text('DMUX' + (' / ' + context if context else ''), style='bold bright_cyan')
    text = Text()
    theme = values.get('theme', 'cyan-dark')
    palette = theme if isinstance(theme, dict) else PALETTES[theme]
    if values['box_style'] == 'ascii':
        text.append('DMUX\n', style='bold bright_cyan')
    elif lockup:
        for index, line in enumerate(HOME_LOCKUP):
            color = '#0891b2' if theme == 'cyan-dark' and index == 0 else palette['accent']
            text.append(line + '\n', style='bold ' + color)
    else:
        text.append('█▀▄ █▀▄▀█ █ █ ▀▄▀\n', style='bold ' + ('#0891b2' if theme == 'cyan-dark' else palette['accent']))
        text.append('█▄▀ █ ▀ █ █▄█ ▄▀▄\n', style='bold ' + palette['accent'])
    if not tagline:
        text.rstrip()
        return text
    text.append('your experiments, one terminal', style='cyan')
    if context:
        text.append(' / ' + context, style='grey70')
    return text


def timestamp(value, clock='local'):
    return datetime.fromtimestamp(value, timezone.utc if clock == 'UTC' else None).isoformat(timespec='seconds')


class Appearance:
    """Translate existing render segments, preserving modules and semantic text."""
    def __init__(self, renderable, settings):
        self.renderable, self.settings = renderable, settings

    def __rich_console__(self, console, options):
        from rich.segment import Segment
        from rich.style import Style
        values = self.settings.values
        palette = values['theme'] if isinstance(values['theme'], dict) else PALETTES[values['theme']]
        for segment in console.render(self.renderable, options):
            text, style, control = segment
            if values['theme'] == 'light' and not control:
                style = Style(color='#111827', bgcolor='#ffffff') + (style or Style())
                if style.color and style.color.name in ('white', 'bright_white'):
                    style = style + Style(color='#111827')
            if values['box_style'] == 'ascii' and not control:
                def plain(char):
                    if 'BOX DRAWINGS' in unicodedata.name(char, ''):
                        name = unicodedata.name(char)
                        return '-' if 'HORIZONTAL' in name and 'VERTICAL' not in name and 'DOWN' not in name and 'UP' not in name else '|' if 'VERTICAL' in name and 'HORIZONTAL' not in name else '+'
                    return {'›': '>', '—': '-', '·': '.', '…': '.', '▏': '|', '↑': '^', '↓': 'v', '✓': '+', '×': 'x'}.get(char, char)
                text = ''.join(plain(char) for char in text)
            if style and style.color:
                name = style.color.name
                token = ('accent' if name in ('cyan', 'bright_cyan') else
                         'ok' if name in ('green', 'bright_green') else
                         'warning' if name == 'yellow' else 'error' if name == 'red' else
                         'muted' if name.startswith('grey') else None)
                if token:
                    style = style + Style(color=palette[token])
                if style.bgcolor and style.bgcolor.name == 'cyan':
                    style = style + Style(bgcolor=palette['selected'])
            yield Segment(text, style, control)


# Argparse keeps --version usable without importing optional presentation code
# until the action is actually invoked.
class VersionAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from ._version import __version__
        from .settings import from_args
        try:
            from rich.console import Console
            settings = from_args(namespace)
            console = Console()
            if console.is_terminal:
                console.print(Appearance(wordmark(settings, context='VERSION'), settings))
        except (ImportError, ValueError, OSError):
            pass
        print(f'dmux {__version__}')
        parser.exit()
