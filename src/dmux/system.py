"""Read-only process, GPU, and disk telemetry."""
from __future__ import annotations

from pathlib import Path
import subprocess

def running_processes(adapter) -> list[dict]:
    """Find only processes explicitly named by an adapter."""

    import psutil

    processes: list[dict] = []
    for process in psutil.process_iter(["pid", "cmdline", "create_time", "status"]):
        try:
            info = process.info
            command = info["cmdline"] or []
            candidates = [Path(value).name for value in command[1:]]
            script = next((name for name in candidates if name in adapter.tracked_scripts), None)
            if script is None or info["status"] == psutil.STATUS_ZOMBIE:
                continue
            cwd = Path(process.cwd())
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
        devices = []
        for line in result.stdout.splitlines():
            name, utilization, used, total = [value.strip() for value in line.rsplit(",", 3)]
            devices.append(
                {
                    "name": name,
                    "utilization": float(utilization),
                    "used_gib": float(used) / 1024,
                    "total_gib": float(total) / 1024,
                }
            )
        return {"devices": devices, "error": None}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"devices": [], "error": f"GPU telemetry unavailable ({type(exc).__name__})"}
