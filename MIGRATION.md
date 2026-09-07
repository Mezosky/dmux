# Connect an existing ML project to dmux

dmux observes the files your project already generates. Training, evaluation,
data preparation, checkpoint export, and other stages do not need to import
dmux or share a particular model framework.

## Describe your existing outputs

Create a separate monitoring directory, such as `/work/my-project/monitor`,
and place a `plan.json` there. For a worker that already writes a JSON counter:

```json
{
  "name": "My experiments",
  "project_root": "/work/my-project",
  "results_dir": "/data/my-results",
  "experiments": [{"tag": "trial-1", "label": "First trial"}],
  "tasks": [
    {
      "experiment": "trial-1",
      "stage": "train",
      "directory": "trial-1",
      "progress": {
        "type": "json",
        "path": "progress.json",
        "current_field": "training.step",
        "total_field": "training.total"
      },
      "log": "train.log",
      "outputs": ["progress.json", "train.log"],
      "metadata": {"seed": 7},
      "process": {"script": "train.py", "output_flag": "--output-dir"}
    }
  ]
}
```

Adjust the paths, JSON field names, and process definition to match your existing
project. Other layouts can use JSONL unique records, file/glob counts, and
completion artifacts; see the [plan schema](docs/PLAN_SCHEMA.md). No scheduler,
completion marker, log, or output preview is required unless you configure it.

## Run from any directory

```bash
dmux watch --project-root /work/my-project --plan-dir monitor
dmux json --plan-dir /work/my-project/monitor
```

`--plan-dir` locates the plan; it is not the base for task outputs. Task
directories resolve from `results_dir`, or from the explicit project root when
no results directory is configured. A relative `project_root` declared in the
plan resolves from the plan directory. Nothing resolves from dmux's installation.

Press Enter for details and `t` for explicitly linked tmux sessions. Closing
the dashboard leaves workers running. Stop and session-removal controls remain
separate, confirmed actions.

## Python API

```python
from dmux import Monitor

monitor = Monitor("/work/my-project/monitor")
snapshot = monitor.snapshot()
for experiment in snapshot["experiments"]:
    print(experiment["tag"], experiment["saved"], experiment["expected"])
```

The Python API and CLI both default to the generic filesystem adapter. The UI
uses presentation settings carried by the snapshot, including custom stages,
labels, and units. Domain-specific formats can be provided by an optional
`dmux.adapters` entry point; no external adapter is required for the built-in
JSON, JSONL, log, and file connectors.

## Existing dmux integrations

Earlier generic plans and commands remain accepted:

| Preferred name | Accepted alias |
| --- | --- |
| `--plan-dir` | `--queue` |
| `--experiment` | `--model` |
| Plan `experiments` catalog | `roster` |
| Task/status `experiment` | `run`, `model`, `group` |
| Task/status `stage` | `name` |
| Snapshot `experiments` | `models` |
| Snapshot `plan_dir` | `queue` |
| Snapshot task `experiment` / `stage` | `model` / `name` |

Prototype-specific adapters and standalone wrapper scripts are no longer bundled.
Describe their output files with the generic schema or supply an external adapter.
The earlier implementation remains recoverable from Git history; original
research projects and running experiments are not modified.

Saved counters still represent committed work, not elapsed-time percentages.
Malformed, duplicate, unexpected, and partial JSONL rows remain visible integrity
issues. Completion codes alone cannot prove that all outputs are present, and
stale status JSON cannot prove that a process is alive.
