"""Read explicit scheduler job IDs on the cluster; never submit or cancel jobs."""
from __future__ import annotations

from datetime import datetime, timedelta
import re
import time

from dmux.adapters.base import resolve_path
from dmux.adapters.filesystem import FilesystemAdapter
from dmux.commands import read_command
from dmux.connectors import TextCache
from dmux.refresh import BackgroundRefresh


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}(?:_[0-9]{1,10})?", value):
        raise ValueError("SLURM job ID must be numeric, optionally with one explicit array index")
    return value


class SlurmAdapter(FilesystemAdapter):
    def __init__(self):
        super().__init__()
        self.external_tools, self.background = True, False
        self.text = TextCache(max_bytes=256)
        self.jobs, self.records, self.errors = {}, {}, {}
        self.query_errors = {}
        self.updated, self.error = -float("inf"), None
        self.ids = ()
        self.checked = None
        self.worker = BackgroundRefresh(self._query)

    def configure_observation(self, *, external_tools=True, background=False):
        self.external_tools, self.background = external_tools, background

    def configure(self, plan):
        super().configure({"name": "SLURM", **plan})
        if len(plan["tasks"]) > 128:
            raise ValueError("SLURM plans support at most 128 explicit jobs per poll")
        for task in plan["tasks"]:
            config = task.get("slurm")
            if not isinstance(config, dict) or set(config) - {"job_id", "job_id_file"}:
                raise ValueError("SLURM tasks require slurm.job_id or slurm.job_id_file")
            if ("job_id" in config) == ("job_id_file" in config):
                raise ValueError("Specify exactly one SLURM job_id or job_id_file")
            if "job_id" in config:
                job_id(config["job_id"])
            elif not task.get("directory") or not isinstance(config["job_id_file"], str) or not config["job_id_file"]:
                raise ValueError("SLURM job_id_file requires an explicit task directory and path")

    def prepare_poll(self, tasks, *, now):
        jobs, errors = {}, {}
        for task, path in tasks:
            key = (task["model"], task["name"])
            config = task["slurm"]
            try:
                jobs[key] = job_id(config["job_id"] if "job_id" in config else
                                   self.text.read(resolve_path(config["job_id_file"], path)).strip())
            except (OSError, ValueError) as exc:
                errors[key] = str(exc)
        self.jobs, self.errors = jobs, errors
        ids = tuple(sorted(set(jobs.values())))
        changed = ids != self.ids
        self.ids = ids
        if changed:
            self.records = {}
            self.query_errors = {}
            self.checked = None
        if not self.external_tools:
            self.records, self.error = {}, "Scheduler queries disabled in doctor"
            return
        if self.background:
            result = self.worker.take()
            if result is not None:
                reading, error = result
                if error:
                    self.records, self.error = {}, str(error)
                elif reading[0] == ids:
                    _, self.records, self.query_errors = reading
                    self.error, self.checked = None, time.time()
                else:
                    self.updated = -float("inf")  # Discard a poll for an old selection.
            if changed or time.monotonic() - self.updated >= 5:
                self.worker.request()
                self.updated = time.monotonic()
        elif changed or time.monotonic() - self.updated >= 5:
            try:
                _, self.records, self.query_errors = self._query()
                self.error = None
            except (OSError, ValueError) as exc:
                self.records, self.error = {}, str(exc)
            self.updated = time.monotonic()
            self.checked = time.time()

    def _query(self):
        ids = self.ids
        if not ids:
            return ids, {}, {}
        queue_error = None
        try:
            text = read_command(["squeue", "--noheader", "--array", "--local", "--jobs=" + ",".join(ids),
                                 "--format=%i|%T"])
            records = self._parse(text, ids)
        except (OSError, ValueError) as exc:
            records, queue_error = {}, str(exc)
        missing = sorted(set(ids) - set(records))
        errors = {}
        if missing:
            since = (datetime.now().astimezone() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
            try:
                text = read_command(["sacct", "--noheader", "--parsable2", "--allocations", "--local", "--duplicates",
                                 "--jobs=" + ",".join(missing), "--starttime=" + since,
                                 "--format=JobID%64,State%32"])
                records.update(self._parse(text, missing))
            except (OSError, ValueError) as exc:
                errors = dict.fromkeys(missing, "; ".join(filter(None, (queue_error, str(exc)))))
        if queue_error and not records:
            errors = {identifier: errors.get(identifier, queue_error) for identifier in ids}
        return ids, records, errors

    @staticmethod
    def _parse(text, requested):
        records = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            fields = [field.strip() for field in line.strip().split("|")]
            if len(fields) != 2 or fields[0] not in requested or fields[0] in records:
                raise ValueError("Ambiguous or unexpected SLURM response; selected jobs were not inferred")
            state = fields[1].split()[0] if fields[1].split() else ""
            if not re.fullmatch(r"[A-Z_]+", state):
                raise ValueError("Malformed SLURM state")
            records[fields[0]] = state
        return records

    def inspect_task(self, task, path, cache, trackers, now):
        progress, completion, detail, warnings = super().inspect_task(task, path, cache, trackers, now)
        key = (task["model"], task["name"])
        identifier = self.jobs.get(key)
        state = self.records.get(identifier)
        error = self.errors.get(key) or self.error or self.query_errors.get(identifier)
        task["scheduler"] = {"backend": "slurm", "job_id": identifier, "state": state,
                             "error": error, "checked": self.checked}
        if error or state is None:
            warnings.append(error or "SLURM job not found in the queue or seven-day accounting window")
        message = f"SLURM {identifier or 'job'} reports {state or 'unavailable'}; no remote PID inferred"
        return progress, {"scheduler_state": state, "error": error, "completion": completion}, "; ".join(filter(None, (detail, message))), warnings

    def state(self, task, progress, *, alive=False, exit_code=None, marker=None, stale_active=False):
        marker = marker or {}
        report = marker.get("scheduler_state")
        completed = report == "COMPLETED" and (not task.get("completion") or marker.get("completion"))
        result = super().state({**task, "completion": True}, progress, alive=alive, exit_code=exit_code,
                               marker=True if completed else None, stale_active=stale_active)
        if alive or result in {"invalid", "failed"}:
            return result
        if marker.get("error") or report is None:
            return "unavailable"
        if report in {"RUNNING", "COMPLETING"}:
            return "scheduler running"
        if report in {"PENDING", "CONFIGURING", "SUSPENDED", "REQUEUED", "RESIZING"}:
            return "scheduled"
        if report in {"FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}:
            return "failed"
        if report == "COMPLETED":
            return result
        return "unavailable"
