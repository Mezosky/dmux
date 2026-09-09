"""Single-flight read-only refresh work, with results consumed on the UI thread."""
from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class BackgroundRefresh(Generic[T]):
    """Run at most one bounded poll; retain the displayed value until it finishes.

    No action/control callbacks belong here. Daemon threads do not delay terminal
    restoration on exit, and results are delivered without mutating UI state.
    """

    def __init__(self, read: Callable[[], T]) -> None:
        self.read = read
        self.pending = False
        self.results: Queue[tuple[T | None, Exception | None]] = Queue(maxsize=1)

    def request(self) -> None:
        if self.pending:
            return
        self.pending = True

        def run() -> None:
            try:
                result = self.read()
            except Exception as exc:
                self.results.put((None, exc))
            else:
                self.results.put((result, None))

        Thread(target=run, name="dmux-refresh", daemon=True).start()

    def take(self) -> tuple[T | None, Exception | None] | None:
        try:
            result = self.results.get_nowait()
        except Empty:
            return None
        self.pending = False
        return result
