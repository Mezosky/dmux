"""Read-only process, GPU, and disk telemetry."""
from __future__ import annotations

from pathlib import Path
import math
import subprocess
import time

from .refresh import BackgroundRefresh

def running_processes(adapter, *, table=None, cwd_cache=None) -> list[dict]:
    """Find only processes explicitly named by an adapter."""

    import psutil

    processes: list[dict] = []
    if table is None:
        table = list(psutil.process_iter(["pid", "cmdline", "create_time", "status"]))
    for process in table:
        try:
            info = process.info
            command = info["cmdline"] or []
            candidates = [Path(value).name for value in command[1:]]
            script = next((name for name in candidates if name in adapter.tracked_scripts), None)
            if script is None or info["status"] == psutil.STATUS_ZOMBIE:
                continue
            identity = (info["pid"], info["create_time"])
            if cwd_cache is None:
                cwd = Path(process.cwd())
            else:
                if identity not in cwd_cache:
                    cwd_cache[identity] = Path(process.cwd())
                cwd = cwd_cache[identity]
            destination = adapter.process_destination(script, command, cwd)
            processes.append(
                {
                    "pid": info["pid"],
                    "script": script,
                    "out": str(destination) if destination else None,
                    "started": info["create_time"],
                    "command": command,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return processes


class HostSampler:
    """Share one process enumeration/GPU query across registered projects."""

    def __init__(self, *, background=False):
        self.gpu_worker = BackgroundRefresh(gpu_info) if background else None
        self.table = None
        self.cwd_cache = {}
        self.process_time = self.gpu_time = -float("inf")
        self.devices = {"devices": [], "error": None}

    def processes(self, adapter):
        import psutil

        now = time.monotonic()
        if self.table is None or now - self.process_time >= 2:
            self.table = list(psutil.process_iter(["pid", "cmdline", "create_time", "status"]))
            self.cwd_cache.clear()
            self.process_time = now
        return running_processes(adapter, table=self.table, cwd_cache=self.cwd_cache)

    def gpu(self):
        now = time.monotonic()
        if now - self.gpu_time >= 5:
            if self.gpu_worker is None:
                self.devices = gpu_info()
            else:
                self.gpu_worker.request()
            self.gpu_time = now
        result = self.gpu_worker.take() if self.gpu_worker is not None else None
        if result is not None:
            devices, error = result
            self.devices = devices if error is None else {"devices": [], "error": str(error)}
        return self.devices


def gpu_info() -> dict:
    """Query NVIDIA telemetry without importing an inference framework."""

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"devices": [], "error": f"GPU telemetry unavailable ({type(exc).__name__})"}
    devices, invalid = [], 0
    for line in result.stdout.splitlines():
        try:
            name, utilization, used, total = [value.strip() for value in line.rsplit(",", 3)]
            values = [float(value) for value in (utilization, used, total)]
            if not all(math.isfinite(value) and value >= 0 for value in values):
                raise ValueError("invalid telemetry")
            devices.append({"name": name, "utilization": values[0],
                            "used_gib": values[1] / 1024, "total_gib": values[2] / 1024})
        except ValueError:
            invalid += 1
    return {"devices": devices,
            "error": f"Skipped {invalid} malformed GPU telemetry line(s)" if invalid else None}
