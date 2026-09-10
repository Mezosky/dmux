# Local MLflow and W&B files

dmux observes files already produced by a tracker. Select individual run
directories in a plan; it does not discover runs by scanning a tracking store,
initialize a tracker, sync data, or connect to a server.

## Start with the filesystem adapter

Copy [the MLflow example](../examples/mlflow-plan.json) or
[the W&B example](../examples/wandb-offline-plan.json) to a new
`monitor/plan.json` in your own project. Replace `/work/project`, the run ID,
directory, and metric/param names with values that actually exist. Add tasks
with distinct experiment tags for other runs. No SDK is required.

```bash
dmux watch --adapter filesystem --project-root /work/project --plan-dir monitor
```

For an MLflow file-store run, this previews the explicitly configured metadata
files and plots the selected metric file when you press Enter. The filesystem
adapter does not interpret YAML status or the task's `mlflow` extension; it
cannot claim that a run completed merely because `meta.yaml` exists. Use the
MLflow adapter below to interpret run status.

For W&B, the example reads a local `files/wandb-summary.json` **if present**,
previews selected metadata/config files, and tails `output.log`. Summary `loss`
is only an example field: choose your own logged key. A JSON summary provides
the latest value, not a historical curve. `_step` is a tracker coordinate,
not a committed-record count or an expected total; the example deliberately
does not use it as progress. Add an explicit generic progress/completion
connector only if your workload produces a suitable source.

W&B's SDK v0.21.1 has a [binary local datastore](https://github.com/wandb/wandb/blob/v0.21.1/wandb/sdk/internal/datastore.py)
and a [summary/history sender](https://github.com/wandb/wandb/blob/v0.21.1/wandb/sdk/internal/sender.py).
Do not assume a particular offline run materializes every JSON file or a
`wandb-history.jsonl`. Missing files remain unavailable; dmux does not reconstruct
them from `run-*.wandb`. This is a conditional filesystem recipe, not W&B binary
history support. Exported JSONL/CSV histories can use the ordinary metric reader.

## MLflow file-store adapter

The tiny `dmux_mlflow` package ships alongside the core and is registered through
the `dmux.adapters` entry-point group. Only explicit adapter selection loads it.
It imports neither MLflow nor training frameworks and needs no additional
runtime dependency.

```bash
dmux adapters
dmux watch --adapter mlflow --project-root /work/project --plan-dir monitor
dmux json --adapter mlflow --project-root /work/project --plan-dir monitor
dmux doctor --adapter mlflow --project-root /work/project --plan-dir monitor
```

`dmux adapters` reports whether each discovered adapter can be loaded. A missing
dependency is shown as unavailable with installation guidance; it does not
prevent other adapters from being listed. A plugin using lazy, mode-specific
dependencies must still check those dependencies when that mode is selected.

Each task points directly at one local run directory. The adapter interprets
the numeric status in `meta.yaml` and displays only explicitly requested
`mlflow.params` and `mlflow.tags` (at most 16 of each). It does not follow
`artifact_uri` or infer log filenames. Use normal `outputs`, `metrics`, and
`log` settings to select what you want to inspect.

The compatibility target is [MLflow 3.3.2's file store](https://github.com/mlflow/mlflow/blob/v3.3.2/mlflow/store/tracking/file_store.py)
and its [run-status enum](https://github.com/mlflow/mlflow/blob/v3.3.2/mlflow/protos/service.proto).
Only a flat metadata mapping with plain keys and an integer `status` from 1 to 5
is supported. Other YAML representations, duplicate status keys, unreadable
files, and malformed status values produce visible errors. No general YAML
loader or object construction runs. Text reads are capped at 64 KiB per file
and cached by file identity, timestamps, and size, with at most 128 entries.
An unreadable or removed file cannot reuse a previously successful status.

| Tracker report | Without a matched live process |
| --- | --- |
| RUNNING | Interrupted, with an explicit advisory-status detail |
| SCHEDULED | Queued, or partial if explicit progress already exists |
| FINISHED | Complete when configured counters and completion checks also pass |
| FAILED / KILLED | Failed |
| Missing metadata | Pending/partial with a warning; never inferred complete |
| Malformed metadata | Invalid with a warning |

A matched live process shows running, subject to the existing integrity checks.
Tracker status never supplies a PID. `k`/`K` require the usual explicit `process`
block and identity-checked confirmation. For example, `{"script": "train.py",
"output_flag": "--run-dir"}` works only when that actual process argument
resolves to the task directory. A saved RUNNING report can survive a crash.

### Explicit totals

By default, tracker status is artifact-only: no numeric denominator or progress
percentage is invented. For a workload that also writes a progress counter,
you may explicitly name the parameter containing its expected count:

```json
"progress": {"type": "json", "path": "progress.json", "current_field": "completed"},
"mlflow": {"expected_param": "planned_records"}
```

This reads `params/planned_records`; `progress.json` must already be produced
by your workload, not by dmux or necessarily by MLflow. The param must contain
a non-negative integer. Do not also set `expected` or `total_field`. Missing,
invalid, or conflicting totals are visible integrity errors. Neither `epochs`
nor metric steps are guessed as counts. Result histories never determine state.

### Metric files

Use `type: "whitespace"` with explicit `columns`, as in the example. This is a
generic source format; the core has no MLflow schema or SDK imports. It uses the
same bounded, cached recent window as JSONL/CSV, only inside opened details.
Extra/missing columns and invalid points are reported and excluded.

The example targets ordinary three-column run metrics. MLflow also writes
dataset-qualified rows with two additional columns; a homogeneous file can
declare `columns: ["timestamp", "value", "step", "dataset", "digest"]`.
Mixed row shapes or multiple datasets are not silently combined: use separate
exports when a selected file needs filtering. Logged-model metric files have
a different layout and are outside this run-directory recipe. If steps repeat
or go backward, the existing reader excludes those points with a warning;
omit `x_field` to show append order when repeated steps are intentional.

## Scope and optional SDKs

The `mlflow` extra installs `mlflow-skinny` for SDK compatibility tests and for
callers who need the SDK themselves. Local monitoring never requires or imports
it. CI tests the file layout against 3.3.2 in one Ubuntu job; all jobs exercise
tiny hand-written fixtures without a tracker SDK.

This increment supports scoped watch/snapshot/json and doctor. Global-home
registration currently uses the filesystem adapter; it does not preserve MLflow
adapter selection. Use the explicit commands above for MLflow status handling.

W&B binary history/SDK support and MLflow tracking-server access are future
increments. There is no `wandb` adapter or SDK extra yet. Those modes need their
own bounded connectors, explicit credentials/configuration, and background
refresh behavior before they can be advertised as usable.
