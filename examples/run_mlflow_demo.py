#!/usr/bin/env python
"""Fit a tiny line and log real MLflow metrics; open dmux in a second terminal."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, help="New directory only; default: a fresh temporary directory")
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between steps (default: 0.5)")
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.steps < 1 or not math.isfinite(args.interval) or args.interval < 0:
        parser.error("steps must be positive and interval finite and non-negative")
    try:
        from mlflow import MlflowClient
    except ImportError:
        parser.exit(2, 'This demo uses the real SDK. Install it with: python -m pip install -e ".[mlflow]"\n')

    if args.run_dir is None:
        root = args.project_root.expanduser().absolute() if args.project_root else Path(tempfile.mkdtemp(prefix="dmux-mlflow-"))
        if args.project_root:
            try:
                root.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                parser.error("project-root already exists; choose a new demo directory")
        client = MlflowClient(tracking_uri=(root / "mlruns").as_uri())
        experiment = client.create_experiment("Tiny linear regression")
        run_id = client.create_run(experiment).info.run_id
        run_dir = root / "mlruns" / experiment / run_id
        client.log_param(run_id, "planned_steps", args.steps)
        client.log_param(run_id, "learning_rate", 0.05)
        client.set_tag(run_id, "mlflow.runName", "Fit y = 2x + 1")
        write_json(run_dir / "progress.json", {"completed": 0})
        # The worker may start only once in this freshly created demo run.
        (run_dir / ".dmux-demo-ready").write_text("tiny-linear-demo\n", encoding="utf-8")
        queue = root / "monitor"
        queue.mkdir()
        plan = {"name": "MLflow demo", "project_root": str(root), "unit": "steps",
                "experiments": [{"tag": run_id, "label": "Linear regression · MLflow"}], "tasks": [{
                    "experiment": run_id, "stage": "train", "directory": str(run_dir),
                    "mlflow": {"expected_param": "planned_steps", "params": ["learning_rate"],
                               "tags": ["mlflow.runName"]},
                    "process": {"script": Path(__file__).name, "output_flag": "--run-dir"},
                    "progress": {"type": "json", "path": "progress.json", "current_field": "completed"},
                    "log": "train.log", "outputs": ["progress.json", "model.json", "train.log"],
                    "metrics": [{"label": "MSE", "type": "whitespace", "path": "metrics/mse",
                                 "columns": ["timestamp", "value", "step"], "field": "value",
                                 "x_field": "step", "goal": "min"}]}]}
        with (queue / "plan.json").open("x", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
        print(f"Demo project: {root}\nOpen in another terminal:\n" + shlex.join([
            "dmux", "watch", "--adapter", "mlflow", "--project-root", str(root),
            "--plan-dir", "monitor", "--no-gpu"]), flush=True)
        print("Enter opens the MSE plot. Closing dmux leaves this demo running.", flush=True)
        # Re-exec gives the worker an explicit output argument for real PID matching.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--run-dir", str(run_dir),
                                 "--steps", str(args.steps), "--interval", str(args.interval)])

    run_dir = args.run_dir.resolve()
    try:
        marker = run_dir / ".dmux-demo-ready"
        if marker.read_text(encoding="utf-8") != "tiny-linear-demo\n":
            raise ValueError("invalid demo marker")
        marker.unlink()
    except (OSError, ValueError):
        parser.error("worker requires a fresh run prepared by this demo")
    client = MlflowClient(tracking_uri=run_dir.parents[1].as_uri())
    run_id = run_dir.name
    weight, bias = 0.0, 0.0
    samples = [(-1.0, -1.0), (0.0, 1.0), (1.0, 3.0)]
    try:
        with (run_dir / "train.log").open("x", encoding="utf-8") as log:
            for step in range(1, args.steps + 1):
                errors = [(x, weight * x + bias - y) for x, y in samples]
                weight -= 0.05 * 2 * sum(x * error for x, error in errors) / len(samples)
                bias -= 0.05 * 2 * sum(error for _, error in errors) / len(samples)
                mse = sum((weight * x + bias - y) ** 2 for x, y in samples) / len(samples)
                client.log_metric(run_id, "mse", mse, timestamp=time.time_ns() // 1_000_000, step=step)
                write_json(run_dir / "progress.json", {"completed": step})
                log.write(f"step={step} mse={mse:.6f}\n")
                log.flush()
                time.sleep(args.interval)
        write_json(run_dir / "model.json", {"weight": weight, "bias": bias})
        client.set_terminated(run_id, status="FINISHED")
    except BaseException:
        client.set_terminated(run_id, status="FAILED")
        raise
    print(f"Finished {args.steps} steps. Reopen the printed dmux command to inspect the results.")


if __name__ == "__main__":
    main()
