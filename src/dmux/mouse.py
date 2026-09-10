"""Cell-based hit testing for the last rendered frame, independent of layout."""
from dataclasses import dataclass

from .terminal import UP, DOWN


@dataclass(frozen=True)
class MouseEvent:
    button: int
    x: int  # Zero-based terminal cells, not Unicode character offsets.
    y: int
    pressed: bool = True


def target(kind, value, style=''):
    from rich.style import Style
    return Style.parse(style) + Style(meta={'dmux_action': (kind, value)})


def help_hint():
    from rich.text import Text
    return Text('? help', style=target('key', '?', 'grey70'), no_wrap=True)


class MouseMap:
    def __init__(self):
        self.regions, self.size = [], None

    def frame(self, renderable):
        return MouseFrame(renderable, self)

    def action(self, event, size):
        # Ignore clicks against an old layout after resize, and every release,
        # drag, modified click, right/middle button and horizontal wheel event.
        if tuple(size) != self.size or not event.pressed:
            return None
        if not (0 <= event.x < size[0] and 0 <= event.y < size[1]):
            return None
        if event.button in (64, 65):
            return ('key', UP if event.button == 64 else DOWN)
        if event.button != 0:
            return None
        return next((action for y, start, end, action in self.regions
                     if y == event.y and start <= event.x < end), None)


class MouseFrame:
    def __init__(self, renderable, screen):
        self.renderable, self.screen = renderable, screen

    def __rich_console__(self, console, options):
        from rich.segment import Segment
        regions = []
        lines = console.render_lines(self.renderable, options, pad=False)
        for y, line in enumerate(lines[:console.height]):
            x = 0
            for segment in line:
                action = segment.style.meta.get('dmux_action') if segment.style else None
                end = min(options.max_width, x + segment.cell_length)
                if action and end > x:
                    regions.append((y, x, end, action))
                x = end
        self.screen.regions = regions
        self.screen.size = (console.width, console.height)
        for line in lines:
            yield from line
            yield Segment.line()
