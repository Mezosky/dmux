# Declarative filesystem plan

The `filesystem` adapter reads `PLAN_DIR/plan.json`. The plan describes existing
outputs; dmux never writes any of the paths in it.
Use `dmux init` to create a new plan and `dmux doctor` to check the connection.
Use `dmux add /path/to/project` to register it for the global `dmux` home.
`dmux watch` and explicit project flags open a scoped plan without registration.

## Minimal example

Save this as `monitor/plan.json`, changing the project root and results location:

```json
{
  "project_root": "/work/my-project",
  "results_dir": "outputs",
  "tasks": [{
    "experiment": "baseline",
    "stage": "train",
    "directory": ".",
    "progress": {"type": "json", "path": "progress.json"}
  }]
}
```

With `outputs/progress.json` containing `{"current": 3, "total": 10}`, run
`dmux --project-root /work/my-project`. This shows 3/10 saved; omitting `total`
shows a count without a percentage. Nothing else is required. The same
[starter file](../examples/plan.json) is included under `examples/`.

## Advanced example

```json
{
  "name": "Vision sweep",
  "project_root": "/srv/ml/vision",
  "unit": "epochs",
  "entity_heading": "EXPERIMENT",
  "disk_warning_gib": 10,
  "experiments": [
    {"tag": "resnet50", "label": "ResNet-50"}
  ],
  "tasks": [
    {
      "experiment": "resnet50",
      "stage": "train",
      "label": "Train classifier",
      "directory": "outputs/resnet50",
      "expected": 90,
      "log": "train.log",
      "progress": {
        "type": "jsonl",
        "path": "metrics.jsonl",
        "identity": ["epoch"],
        "semantic_identity": ["epoch", "split"],
        "status_field": "status",
        "valid_statuses": ["ok"],
        "excluded_statuses": ["skipped"],
        "group_field": "split",
        "group_label": "Dataset split",
        "expected_by_group": {"train": 90},
        "allowed_values": {"split": ["train"]}
      },
      "completion": {
        "type": "file",
        "path": "DONE",
        "required": ["model.safetensors"]
      },
      "process": {
        "script": "train.py",
        "output_flag": "--output-dir"
      }
    }
  ]
}
```

`experiment` names a run; `run`, `model`, and `group` are accepted aliases.
`name` is accepted as an alias for `stage`. The optional `experiments` catalog
supplies display labels; `roster` is accepted for earlier plans. An experiment
listed without any tasks is shown as blocked, with an optional `reason`.
Task-local `label` values can differ even when tasks share a stage. Optional
`stage_labels` supplies the shared headings used by the overview columns.
Treat experiment tags as stable execution IDs; use display labels for renaming.
For another execution, choose a distinct tag and output directory. Duplicate
canonical experiment/stage identities are rejected instead of merged.

## Path rules

- `--plan-dir` locates the directory containing `plan.json`; `--queue` is an alias.
  Without the flag, dmux checks the selected project root for `plan.json`, then
  `monitor/plan.json`. A root-level plan takes precedence if both exist.
- An explicit `--project-root` has highest precedence.
- Otherwise, `project_root` in the plan is used. A relative declared root is
  resolved from the plan directory.
- Without either, relative task directories use the launch directory and dmux
  emits a warning. Portable integrations should always choose one of the first
  two forms.
- `directory` is relative to the project root. Progress, completion, required,
  and log paths are relative to that task directory.

An optional `results_dir` (in the plan or via `--results-dir`) changes the base
for task directories. Relative result locations resolve from the project root.
The CLI value overrides the default project's plan value. Without either, the
original project-relative behavior is preserved.

## Multiple projects

Each task can select a named project whose code and outputs live separately:

```json
{
  "projects": {
    "vision": {
      "root": "/work/vision",
      "results_dir": "/data/vision-results",
      "tmux_session": "vision-chat",
      "metadata": {"team": "perception"}
    },
    "language": {
      "root": "/work/language",
      "results_dir": "outputs"
    }
  },
  "tasks": [
    {
      "project": "vision",
      "experiment": "clip",
      "stage": "evaluate",
      "directory": "run-1",
      "progress": {"type": "json", "path": "progress.json"},
      "metadata": {"seed": 7},
      "outputs": ["metrics.json", "predictions.jsonl"]
    }
  ]
}
```

Project roots resolve relative to the plan's resolved project root. Each result
location resolves from its own project's root. Named projects retain their own
locations even when the CLI overrides the default project's result directory.
Runs become `PROJECT/RUN` tags (for example `vision/clip`) to avoid merging runs
with identical names. Experiment catalog and status records can also declare `project`.
An empty results directory is allowed; missing artifacts remain pending.

## Detail metadata and outputs

`metadata` objects can be declared on the plan, project, and task; more specific
values override earlier ones. Task `outputs` lists optional paths or patterns
relative to its result directory. The Enter detail view shows at most eight
matched files, with bounded previews for JSON/JSONL/log/text/CSV files. Other
artifacts are never deserialized.

## Optional result metrics

Each task can declare a `metrics` list for small numeric summaries and history
plots. Sources are read **only when that experiment's detail view is open**.
They never affect progress or completion. JSON objects/arrays, JSONL, and CSV
are supported; fields, optimization direction, and units are user-selected.
See the [result configuration guide](RESULTS.md) for the full contract.

```json
"metrics": [
  {"label": "Loss", "type": "jsonl", "path": "history.jsonl",
   "field": "loss", "x_field": "step", "goal": "min"}
]
```

## Session links

Use `tmux_session` on a project or task, or supply a plan-wide configuration:

```json
{"tmux": {"socket": "/tmp/my-tmux.sock", "links": {"vision/clip": "vision-chat"}}}
```

Task links override project defaults; a `tmux.links` entry overrides both and
an explicit `--tmux-link TAG=TARGET` overrides the plan. Relative socket paths
are resolved from the plan directory. Links select navigation destinations and
never prove that an experiment process is alive.

An absolute plan location does not change the base for relative task outputs;
those still use the project's explicitly configured root or results directory.

## Progress connectors

### JSONL

`type: "jsonl"` incrementally reads only newline-terminated JSON objects.
Unterminated final writes are held back until committed. File replacement,
rotation, or truncation resets the cursor and counts. Fields:

- `path`: JSONL path relative to the task directory.
- `identity`: fields defining the primary unique record. Default: `["id"]`.
- `semantic_identity`: optional second identity that catches the same logical
  evaluation written under another primary ID.
- `status_field`, `valid_statuses`, `excluded_statuses`: optional outcome rules.
- `group_field`, `group_label`, `expected_by_group`: optional detail-panel split.
- `allowed_values`: optional field-to-allowed-values map; violations are flagged
  and excluded from saved/accepted counts.

Malformed, duplicate, unexpected, and partial records never silently inflate
progress. Use JSONL for scientific evaluation counts where uniqueness matters.

### JSON

`type: "json"` reads a periodically rewritten JSON document through a last-good
cache. `current_field` and `total_field` accept dotted paths such as
`training.step`. This connector reports a producer-supplied counter; it does not
claim row-level uniqueness. It is suitable for steps, epochs, or scheduler state.
Both counters must be non-negative integers, not precomputed percentages.
The defaults are `current` and `total`; an absent default total is allowed.
If `total_field` is explicitly configured it must resolve to an integer, or the
task must provide a fallback `expected` count. Unknown or malformed field names
are reported by `dmux doctor` with the available integer fields.

### Files

`type: "files"` counts unique files matching `glob` below the task directory.
It is useful for checkpoints, shards, or exported result partitions. Scans are
bounded by `max_files` (default 100,000); exceeding the bound is flagged instead
of reporting a silently incomplete count.

### Unknown totals

For JSONL and files, omit `expected` when no reliable total is available. For
JSON, omit `total_field` and leave the default `total` absent. Such stages show
saved counts, with `expected: null` in snapshots, and never a percentage or ETA.
Completion cannot be inferred from the count alone. An explicit completion
artifact may still establish completion. A counted stage with an unknown total
also makes its experiment and whole-plan denominator unknown; known stages
retain their own percentages. Artifact-only stages do not enter numeric totals.

## Completion artifacts

A task without a numeric counter can use `completion`:

- `type: "file"`: `path` must exist; every `required` companion must also exist.
- `type: "json"`: the JSON value at dotted `field` must equal `equals`.

Artifact-only tasks display state, never an invented percentage.
When a numeric task also declares `completion`, both the exact count and the
validated completion artifact are required.

## Optional scheduler state

If present, `status.json` may contain an advisory `active` record and
`completed_tasks`. `completion.json` may contain final task exit records. Each
record uses `experiment`/`stage` (earlier aliases are also accepted). Advisory state never proves
liveness; dmux checks actual processes declared by `queue_process` and each
task's `process` block.

Standalone experiments need only their task's `process` block. Set
`queue_process` only when a scheduler should be monitored, for example
`{"script": "scheduler.py", "output_flag": "--plan-dir"}`. Its output argument
must resolve to this plan directory. Only explicitly configured schedulers
produce missing-scheduler warnings.

## Logs and processes

`log` is tailed with bounded reads and ANSI/control sanitization. Process
definitions may specify `script`, `output_flag`, and `default_output`. Only an
exact configured script basename is considered, and destination paths must match
the resolved task or plan-directory path. Both `--out PATH` and `--out=PATH`
forms are supported. PID discovery cannot see inaccessible processes or remote
hosts and does not infer liveness from counters alone. Logs are read only when a task declares
`log`; dmux does not guess filenames.

`pause_file` is optional and relative to the plan directory. dmux never creates or removes
it; if omitted, the generic adapter does not infer pause state from filenames.
