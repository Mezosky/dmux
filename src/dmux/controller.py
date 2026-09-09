"""Unit-testable experiment navigation and explicit action state."""
from __future__ import annotations

import math
import time

from .bindings import action_key
from .experiment_view import PreviewReader
from .metrics import MetricReader
from .process_actions import ProcessActionError, prepare_stop, stop
from .session_browser import SessionBrowser
from .ui import selected_task


class DashboardController:
    def __init__(self, snapshot, adapter, navigator, *, selected=None, stage=None,
                 entry=None, return_home=False):
        self.snapshot, self.adapter, self.navigator = snapshot, adapter, navigator
        self.selected, self.stage = selected, stage
        self._entry, self._return_home = entry, return_home
        self.running, self.detailed, self.help = True, entry == "detail", False
        self.pending_stop, self.stop_confirmation, self.notice = None, "", None
        self.metric_reader, self.metric_offset = MetricReader(), 0
        self.preview_reader, self.hidden = PreviewReader(), set()
        self.updated = time.monotonic()
        self.picker = (SessionBrowser(navigator, snapshot["tasks"], preferred=selected, background=True)
                       if entry == "tmux" else None)

    def visible_snapshot(self):
        visible = [m for m in self.snapshot["models"] if m["tag"] not in self.hidden]
        return {**self.snapshot, "models": visible, "experiments": visible, "hidden_count": len(self.hidden),
                "active": self.snapshot.get("active") if (self.snapshot.get("active") or {}).get("model") not in self.hidden else None}

    def current_model(self):
        visible = self.visible_snapshot()
        selected_tag = self.selected if any(m["tag"] == self.selected for m in visible["models"]) else None
        return selected_tag or (visible.get("active") or {}).get("model") or next(
            (m["tag"] for m in visible["models"]), None)

    def key(self, key):
        """Handle one decoded key, returning only an explicit tmux handoff."""
        if self.pending_stop is None and self.picker is None and not self.help:
            # Keep Esc distinct: it closes detail but does not quit the dashboard.
            if key != "\x1b":
                key = action_key(key, "detail" if self.detailed else "dashboard")
        if key == "\x03":
            self.running = False
        elif self.pending_stop is not None:
            if key == "\x1b":
                self.pending_stop, self.stop_confirmation = None, ""
            elif key in "\r\n":
                try:
                    self.notice = stop(self.pending_stop, confirmation=self.stop_confirmation)
                except ProcessActionError as exc:
                    self.notice = str(exc)
                self.pending_stop, self.stop_confirmation = None, ""
                self.updated = -math.inf
            elif key in "\b\x7f":
                self.stop_confirmation = self.stop_confirmation[:-1]
            elif key.isprintable():
                self.stop_confirmation += key
        elif self.picker is not None:
            action = self.picker.key(key)
            if action:
                self.notice = self.picker.notice
                self.picker = None
                if action[0] == "open":
                    return action[1]
                if self._entry == "tmux" and self._return_home:
                    self.running = False
        elif self.help:
            if key in ("?", "q", "Q", "\x1b"):
                self.help = False
        elif key == "?":
            self.help = True
        elif key in "qQ\x1b":
            if self.detailed:
                self.detailed = False
                self.metric_reader.clear()
                self.preview_reader.clear()
                if self._return_home:
                    self.running = False
            elif key in "qQ":
                self.running = False
        elif key in "\r\n" and self.current_model():
            self.selected, self.detailed = self.current_model(), True
        elif key == "m" and self.detailed:
            self.metric_offset += 1
        elif key in "kK" and self.current_model():
            task = selected_task(self.snapshot, self.current_model(), self.stage)
            try:
                self.pending_stop = prepare_stop(self.snapshot, self.current_model(),
                                            task["name"] if key == "k" and task else None)
                self.stop_confirmation = ""
            except ProcessActionError as exc:
                self.notice = str(exc)
        elif key in "xX" and self.current_model():
            self.hidden.add(self.current_model())
            self.selected, self.stage, self.detailed = None, None, False
            self.metric_reader.clear()
            self.preview_reader.clear()
            self.notice = "Tab hidden; its processes continue. Press u to restore hidden tabs."
        elif key in "uU":
            self.hidden.clear()
            self.notice = None
        elif key in "tT":
            self.notice = None
            current = self.current_model()
            self.picker = SessionBrowser(self.navigator, self.snapshot["tasks"], preferred=current, background=True)
        elif key in "aA":
            self.selected, self.stage = None, None
        elif key in "nNpP" and self.visible_snapshot()["models"]:
            tags_in_order = [model["tag"] for model in self.visible_snapshot()["models"]]
            current = self.current_model()
            direction = 1 if key in "nN" else -1
            self.selected = tags_in_order[
                (tags_in_order.index(current) + direction) % len(tags_in_order)
            ]
            self.stage = None
            self.metric_offset = 0
            self.metric_reader.clear()
            self.preview_reader.clear()
        elif key in "[]":
            stages = tuple(dict.fromkeys(t["name"] for t in self.snapshot["tasks"]
                                         if t["model"] == self.current_model())) or self.adapter.presentation.stages
            task = selected_task(self.snapshot, self.current_model(), self.stage)
            current_stage = task["name"] if task else stages[0]
            direction = 1 if key == "]" else -1
            self.stage = stages[(stages.index(current_stage) + direction) % len(stages)]
            self.metric_offset = 0
        elif key in "rR":
            self.updated, self.notice = -math.inf, None
