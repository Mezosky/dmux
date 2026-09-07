# Declarative filesystem plan

The `filesystem` adapter reads `PLAN_DIR/plan.json`. The plan describes existing
outputs; dmux never writes any of the paths in it.

## Minimal example

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

## Path rules

- `--plan-dir` locates the directory containing `plan.json`; `--queue` is an alias.
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

### Files

`type: "files"` counts unique files matching `glob` below the task directory.
It is useful for checkpoints, shards, or exported result partitions. Scans are
bounded by `max_files` (default 100,000); exceeding the bound is flagged instead
of reporting a silently incomplete count.

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
the resolved task or plan-directory path. Logs are read only when a task declares
`log`; dmux does not guess filenames.

`pause_file` is optional and relative to the plan directory. dmux never creates or removes
it; if omitted, the generic adapter does not infer pause state from filenames.
