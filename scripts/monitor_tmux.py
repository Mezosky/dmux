"""Compatibility exports for the original tmux helper module."""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from dmux.tmux import PANE_FORMAT, TmuxNavigator, associate, parse_links, parse_panes, render_picker

__all__ = [
    "PANE_FORMAT",
    "TmuxNavigator",
    "associate",
    "parse_links",
    "parse_panes",
    "render_picker",
]

