#!/usr/bin/env python
"""Run four tiny heterogeneous ML workloads and emit dmux-compatible files.

The models are dependency-free and deterministic: a CLIP-like dual encoder, a
bigram language model, a tabular regressor, and an audio classifier. They test
monitoring contracts, not benchmark quality.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import shlex
import sys
import time


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


EXPERIMENTS = ("tiny_clip", "tiny_llm", "tabular", "audio")


def plan(steps: int, queue: Path, results_dir: Path | None = None) -> dict:
    return {
        "name": "Tiny heterogeneous model zoo",
        "unit": "records",
        "entity_heading": "EXPERIMENT",
        "project_root": str(queue.parent),
        "results_dir": str(results_dir or queue.parent / "runs"),
        "roster": [
            {"tag": "tiny_clip", "label": "Tiny CLIP · vision+text"},
            {"tag": "tiny_llm", "label": "Bigram LM · language"},
            {"tag": "tabular", "label": "Linear regressor · tabular"},
            {"tag": "audio", "label": "Frequency classifier · audio"},
        ],
        "tasks": [
            {
                "run": "tiny_clip",
                "stage": "evaluate",
                "label": "Zero-shot image/text matching",
                "directory": "tiny_clip",
                "expected": steps,
                "log": "evaluate.log",
                "progress": {
                    "type": "jsonl",
                    "path": "predictions.jsonl",
                    "identity": ["sample_id"],
                    "semantic_identity": ["image_id", "prompt"],
                    "status_field": "status",
                    "valid_statuses": ["ok"],
                    "group_field": "split",
                    "group_label": "Dataset split",
                    "expected_by_group": {"validation": steps},
                    "allowed_values": {"split": ["validation"]},
                },
            },
            {
                "run": "tiny_llm",
                "stage": "train",
                "label": "Train bigram language model",
                "directory": "tiny_llm",
                "expected": steps,
                "log": "train.log",
                "progress": {
                    "type": "json",
                    "path": "progress.json",
                    "current_field": "training.epoch",
                    "total_field": "training.total_epochs",
                },
                "completion": {"type": "file", "path": "model.json"},
            },
            {
                "run": "tabular",
                "stage": "train",
                "label": "Fit linear regressor",
                "directory": "tabular",
                "expected": steps,
                "progress": {"type": "files", "glob": "checkpoint-*.json"},
            },
            {
                "run": "audio",
                "stage": "evaluate",
                "label": "Classify synthetic frequencies",
                "directory": "audio",
                "completion": {
                    "type": "file",
                    "path": "DONE",
                    "required": ["metrics.json"],
                },
            },
        ],
    }


def update_status(queue: Path, run: str | None, stage: str | None) -> None:
    active = {"run": run, "stage": stage} if run and stage else None
    write_json(queue / "status.json", {"active": active, "completed_tasks": []})


def cosine(left, right) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator


def run_tiny_clip(root: Path, steps: int, delay: float) -> None:
    """Evaluate a tiny dual encoder over synthetic color images and prompts."""

    output = root / "tiny_clip"
    output.mkdir(parents=True, exist_ok=True)
    colors = {
        "red": (1.0, 0.05, 0.05),
        "green": (0.05, 1.0, 0.05),
        "blue": (0.05, 0.05, 1.0),
    }
    names = tuple(colors)
    for index in range(steps):
        target = names[index % len(names)]
        scores = {name: cosine(colors[target], embedding) for name, embedding in colors.items()}
        prediction = max(scores, key=scores.get)
        row = {
            "sample_id": f"sample-{index:03d}",
            "image_id": f"solid-{target}-{index:03d}",
            "prompt": f"a {target} square",
            "split": "validation",
            "prediction": prediction,
            "correct": prediction == target,
            "similarity": scores[prediction],
            "status": "ok",
        }
        with (output / "predictions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        with (output / "evaluate.log").open("a", encoding="utf-8") as handle:
            handle.write(f"image={index} target={target} predicted={prediction}\n")
        time.sleep(delay)


def run_tiny_llm(root: Path, steps: int, delay: float) -> None:
    """Train a smoothed word-bigram language model and report perplexity."""

    output = root / "tiny_llm"
    output.mkdir(parents=True, exist_ok=True)
    tokens = "the cat sat on the mat the dog sat by the cat".split()
    vocabulary = sorted(set(tokens))
    transitions: dict[str, Counter] = defaultdict(Counter)
    for epoch in range(1, steps + 1):
        for left, right in zip(tokens, tokens[1:]):
            transitions[left][right] += 1
        negative_log_likelihood = 0.0
        for left, right in zip(tokens, tokens[1:]):
            total = sum(transitions[left].values())
            probability = (transitions[left][right] + 1) / (total + len(vocabulary))
            negative_log_likelihood -= math.log(probability)
        perplexity = math.exp(negative_log_likelihood / (len(tokens) - 1))
        write_json(
            output / "progress.json",
            {
                "training": {
                    "epoch": epoch,
                    "total_epochs": steps,
                    "perplexity": perplexity,
                }
            },
        )
        with (output / "train.log").open("a", encoding="utf-8") as handle:
            handle.write(f"epoch={epoch} perplexity={perplexity:.4f}\n")
        time.sleep(delay)
    write_json(output / "model.json", {left: dict(counts) for left, counts in transitions.items()})


def run_tabular_regression(root: Path, steps: int, delay: float) -> None:
    """Fit y=2x+1 by gradient descent and checkpoint every step."""

    output = root / "tabular"
    output.mkdir(parents=True, exist_ok=True)
    points = [(-1.0, -1.0), (0.0, 1.0), (1.0, 3.0), (2.0, 5.0)]
    weight = bias = 0.0
    for step in range(1, steps + 1):
        gradient_w = sum(2 * ((weight * x + bias) - y) * x for x, y in points) / len(points)
        gradient_b = sum(2 * ((weight * x + bias) - y) for x, y in points) / len(points)
        weight -= 0.1 * gradient_w
        bias -= 0.1 * gradient_b
        mse = sum(((weight * x + bias) - y) ** 2 for x, y in points) / len(points)
        write_json(
            output / f"checkpoint-{step:03d}.json",
            {"step": step, "weight": weight, "bias": bias, "mse": mse},
        )
        time.sleep(delay)


def run_audio_classifier(root: Path) -> None:
    """Classify low/high synthetic sine waves by zero-crossing rate."""

    output = root / "audio"
    output.mkdir(parents=True, exist_ok=True)
    correct = 0
    examples = []
    for label, cycles in (("low", 2), ("high", 9)):
        samples = [math.sin(2 * math.pi * cycles * index / 64) for index in range(64)]
        crossings = sum((left < 0) != (right < 0) for left, right in zip(samples, samples[1:]))
        prediction = "high" if crossings > 10 else "low"
        correct += prediction == label
        examples.append({"label": label, "prediction": prediction, "zero_crossings": crossings})
    write_json(output / "metrics.json", {"accuracy": correct / len(examples), "examples": examples})
    (output / "DONE").touch()


def run_experiment(name: str, results: Path, steps: int, delay: float) -> None:
    workloads = {
        "tiny_clip": lambda: run_tiny_clip(results, steps, delay),
        "tiny_llm": lambda: run_tiny_llm(results, steps, delay),
        "tabular": lambda: run_tabular_regression(results, steps, delay),
        "audio": lambda: run_audio_classifier(results),
    }
    workloads[name]()
    print(f"Completed {name}; results: {results / name}", flush=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--results-dir", type=Path, help="Output location; default PROJECT/runs")
    parser.add_argument("--tmux", action="store_true", help="Create a session per experiment with chat and worker windows")
    parser.add_argument("--tmux-socket", type=Path, help="Server to create demo sessions on")
    parser.add_argument("--session-prefix", default="dmux", help="Prefix for the four session names")
    args = parser.parse_args(argv)
    if args.steps < 1 or not math.isfinite(args.delay) or args.delay < 0:
        parser.error("--steps must be positive and --delay must be finite and non-negative")
    root = args.project_root.expanduser().resolve()
    queue = root / "monitor"
    results = args.results_dir.expanduser() if args.results_dir else root / "runs"
    if not results.is_absolute():
        results = root / results
    results = results.resolve()
    if (queue / "plan.json").exists() or any((results / name).exists() for name in EXPERIMENTS):
        parser.error("Demo outputs already exist; choose a fresh --project-root and --results-dir")
    definition = plan(args.steps, queue, results)
    for task in definition["tasks"]:
        task["metadata"] = {"demo": True, "experiment": task["run"], "steps": args.steps,
                            "description": task["label"]}
        task["outputs"] = {
            "tiny_clip": ["predictions.jsonl", "evaluate.log"],
            "tiny_llm": ["progress.json", "model.json", "train.log"],
            "tabular": ["checkpoint-*.json"],
            "audio": ["metrics.json"],
        }[task["run"]]
    if args.tmux:
        from .sessions import SessionError, SessionManager
        from .tmux import TmuxNavigator

        nav = TmuxNavigator(socket=args.tmux_socket)
        manager = SessionManager(nav)
        links = {name: f"{args.session_prefix}-{name}" for name in EXPERIMENTS}
        # Detect collisions before any work starts. Creation also refuses races.
        existing = {p["session"] for p in nav.snapshot([])["panes"]}
        if existing & set(links.values()):
            parser.error("Demo session name already exists; choose another --session-prefix")
        definition["tmux"] = {"links": links}
        if args.tmux_socket:
            definition["tmux"]["socket"] = str(args.tmux_socket.expanduser().resolve())
        for task in definition["tasks"]:
            task["process"] = {"script": "demo_worker.py", "output_flag": "--out"}
        root.mkdir(parents=True, exist_ok=True)
        created = []
        try:
            # Chat shells keep completed experiments' sessions alive for review.
            for name in EXPERIMENTS:
                sid = manager.create(links[name], project_root=root, results_dir=results / name)
                created.append((name, sid))
            write_json(queue / "plan.json", definition)
            for name, sid in created:
                manager.add_window(sid, name="experiment", project_root=root,
                    results_dir=results / name, command=[sys.executable,
                        str(Path(__file__).with_name("demo_worker.py")), "--experiment", name,
                        "--out", str(results / name), "--steps", str(args.steps),
                        "--delay", str(args.delay), "--keep-window"])
        except SessionError as exc:
            # Do not kill partially created workspaces; report their exact IDs.
            parser.exit(2, f"{exc}\nCreated sessions: {', '.join(sid for _, sid in created) or 'none'}\n")
    else:
        write_json(queue / "plan.json", definition)
        for task in definition["tasks"]:
            update_status(queue, task["run"], task["stage"])
            run_experiment(task["run"], results, args.steps, args.delay)
        update_status(queue, None, None)
    print(f"Tiny model zoo ready at {root}")
    print(f"dmux watch --project-root {shlex.quote(str(root))} --queue monitor")
    if args.tmux:
        print("Press t to browse the linked sessions; chat windows are ready for your AI CLI.")


if __name__ == "__main__":
    main()
