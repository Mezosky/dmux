"""Bounded full-screen log browsing; never reads outside the selected log."""
from pathlib import Path

from .connectors import open_regular
from .terminal import UP, DOWN


class LogView:
    def __init__(self, path, *, limit=500):
        self.path = Path(path) if path else None
        self.limit, self.follow, self.offset = limit, True, 0
        self.query, self.searching, self.error = '', False, None
        self.lines, self.stamp, self.limited = [], None, False

    def refresh(self):
        if self.path is None:
            self.error = 'No log configured for this stage'
            return
        if not self.follow and self.stamp is not None:
            return  # Freeze the bounded window while the user searches or scrolls.
        try:
            with open_regular(self.path) as handle:
                import os
                info = os.fstat(handle.fileno())
                stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size, self.limit)
                if stamp == self.stamp:
                    return
                start = max(0, info.st_size - 262_144)
                handle.seek(start)
                raw = handle.read(262_144)
            if start:
                raw = raw.partition(b'\n')[2]
            lines = raw.decode('utf-8', errors='replace').splitlines()
            # Strip terminal escape/control bytes; log contents are data.
            import re
            lines = [re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line) for line in lines]
            self.lines = [''.join(c for c in line if c.isprintable() or c == '\t') for line in lines[-self.limit:]]
            self.limited = bool(start or len(lines) > self.limit)
            self.stamp, self.error = stamp, None
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.lines, self.stamp = [], None

    def key(self, key, *, height=40):
        if self.searching:
            if key in ('\r', '\n', '\x1b'):
                self.searching = False
            elif key in ('\b', '\x7f'):
                self.query = self.query[:-1]
            elif key.isprintable():
                self.query += key
            self.offset = 0
        elif key in ('q', '\x1b', 'l'):
            return True
        elif key == '/':
            self.searching, self.follow, self.query = True, False, ''
        elif key == 'f':
            self.follow = not self.follow
            self.offset = 0
        elif key in ('j', DOWN, 'k', UP, '\x04', '\x15'):
            self.follow = False
            delta = max(1, height - 8) if key in ('\x04', '\x15') else 1
            self.offset = max(0, self.offset + (delta if key in ('k', UP, '\x15') else -delta))
        elif key == 'r':
            self.follow, self.stamp = True, None
        return False

    def render(self, *, height=40):
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text
        self.refresh()
        rows = [line for line in self.lines if self.query.casefold() in line.casefold()]
        page = max(1, height - 7)
        self.offset = min(self.offset, max(0, len(rows) - page))
        end = len(rows) - self.offset
        body = rows[max(0, end - page):end]
        heading = f'DMUX / LOG / {"FOLLOW" if self.follow else "FROZEN"}'
        status = f'{len(rows)} matching lines in a bounded {len(self.lines)}-line window'
        if self.limited:
            status += ' (older content omitted)'
        return Panel(Group(Text(str(self.path or 'No configured path'), overflow='ellipsis', no_wrap=True),
                           Text('\n'.join(body) or self.error or 'No matching lines', overflow='ellipsis', no_wrap=True),
                           Text('Search: ' + self.query + ('|' if self.searching else '')),
                           Text(status, style='grey62'),
                           Text('j/k scroll . Ctrl-D/U page . / search . f follow . r latest . q/Esc back')),
                     title=heading, border_style='cyan')
