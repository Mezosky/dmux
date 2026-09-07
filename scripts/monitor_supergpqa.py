#!/usr/bin/env python
"""Compatibility entry point for the original SuperGPQA monitor."""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from dmux.adapters.supergpqa import RowTracker, SuperGPQAAdapter, task_state
from dmux.cli import main as _main
from dmux.connectors import JsonCache, tail_log as _tail_log
from dmux.monitor import Monitor
from dmux.ui import render_dashboard


def tail_log(path, n=3):
    return _tail_log(path, n=n, formatter=SuperGPQAAdapter().format_log)


def main():
    _main(default_adapter="supergpqa")


if __name__ == "__main__":
    main()

