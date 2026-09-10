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


def shadow_wordmark(accent, shadow):
    """Pack a tiny pixel wordmark and its offset shadow into three text rows."""
    from rich.text import Text
    from rich.style import Style
    letters = (
        ('11110', '10001', '10001', '10001', '11110'),  # D
        ('10001', '11011', '10101', '10001', '10001'),  # M
        ('10001', '10001', '10001', '10001', '01110'),  # U
        ('10001', '01010', '00100', '01010', '10001'),  # X
    )
    rows = ['0'.join(letter[y] for letter in letters) for y in range(5)]
    width = len(rows[0]) + 1

    def pixel(x, y):
        if 0 <= y < 5 and 0 <= x < width - 1 and rows[y][x] == '1':
            return accent
        if 1 <= y <= 5 and 1 <= x < width and rows[y - 1][x - 1] == '1':
            return shadow
        return None

    text = Text()
    for y in range(0, 6, 2):
        for x in range(width):
            top, bottom = pixel(x, y), pixel(x, y + 1)
            if top == bottom:
                text.append('█' if top else ' ', style=Style(color=top))
            elif top:
                text.append('▀', style=Style(color=top, bgcolor=bottom))
            else:
                text.append('▄', style=Style(color=bottom))
        text.append('\n')
    return text


def wordmark(settings=None, *, context='', compact=False, lockup=True, tagline=True):
    from rich.text import Text
    values = settings.values if settings is not None else {'banner': True, 'box_style': 'unicode'}
    if compact or not values['banner'] or values.get('logo_style') == 'minimal':
        return Text('DMUX' + (' / ' + context if context else ''), style='bold bright_cyan')
    text = Text()
    theme = values.get('theme', 'cyan-dark')
    palette = theme if isinstance(theme, dict) else PALETTES[theme]
    if values['box_style'] == 'ascii':
        text.append('DMUX\n', style='bold bright_cyan')
    elif values.get('logo_style') == 'shadow':
        text.append_text(shadow_wordmark(palette['accent'], '#075466' if theme == 'cyan-dark' else palette['muted']))
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
                # A palette for an already-light terminal; never paint isolated
                # white text rectangles over the terminal's own background.
                style = Style(color='#111827') + (style or Style())
                if style.color and style.color.name in ('white', 'bright_white'):
                    style = style + Style(color='#111827')
                if style.bgcolor and style.bgcolor.name in ('grey11', 'grey15'):
                    style = style + Style(bgcolor='#e2e8f0')
                elif style.bgcolor and style.bgcolor.name in ('cyan', 'bright_cyan'):
                    style = style + Style(color='#ffffff', bgcolor=palette['selected'])
            if values['box_style'] == 'ascii' and not control:
                def plain(char):
                    if 'BOX DRAWINGS' in unicodedata.name(char, ''):
                        name = unicodedata.name(char)
                        return '-' if 'HORIZONTAL' in name and 'VERTICAL' not in name and 'DOWN' not in name and 'UP' not in name else '|' if 'VERTICAL' in name and 'HORIZONTAL' not in name else '+'
                    return {'›': '>', '—': '-', '·': '.', '●': '*', '…': '.', '▏': '|', '↑': '^', '↓': 'v', '✓': '+', '×': 'x'}.get(char, char)
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
