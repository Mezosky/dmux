"""One demonstration workload, launched independently or in tmux."""
from pathlib import Path
import os
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dmux.demo import EXPERIMENTS, run_experiment


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=EXPERIMENTS, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--delay", type=float, required=True)
    parser.add_argument("--keep-window", action="store_true")
    args = parser.parse_args()
    output = args.out.resolve()
    if output.name != args.experiment:
        parser.error("Output directory must match the experiment name")
    run_experiment(args.experiment, output.parent, args.steps, args.delay)
    if args.keep_window:
        shell = os.environ.get("SHELL", "/bin/sh")
        os.execvpe(shell, [shell, "-i"], os.environ)


if __name__ == "__main__":
    main()
