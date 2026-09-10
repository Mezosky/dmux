# Versioned JSON for scripts and agents

In dmux 0.2, `dmux json` and scoped `--json` default to **schema version 2**.
`dmux home --json` also includes `schema_version: 2`, with its own state-only
report schema. These commands observe existing experiments without modifying
results, registering projects, or starting/stopping jobs.

```bash
dmux json --project-root /work/project --plan-dir monitor --no-gpu
dmux home --json --no-gpu
# Existing scripts can retain the original scoped aliases or home shape:
dmux json --schema-version 1 --project-root /work/project --plan-dir monitor --no-gpu
dmux home --json --schema-version 1 --no-gpu
```

## Stable names and migration

| Version 2 field | Removed version 1 alias |
| --- | --- |
| `plan_dir` | `queue` |
| `experiments` | `models` |
| `tasks[].experiment` | `tasks[].model` |
| `tasks[].stage` | `tasks[].name` |

The same task aliases are removed from `active`. `model_id` is retained as
roster metadata, not an experiment-identity alias. CLI flags and plan-input
aliases remain accepted; this change concerns JSON output only.

The supported Python boundary is `dmux.export_snapshot(monitor.snapshot())`.
`Monitor.snapshot()` retains its version 1 object for existing adapters and UI
callers. Exporting makes a copy, so it cannot alter the dashboard's state.
`export_snapshot(..., version=1)` preserves the original aliases.

## Schemas and semantics

The bundled [scoped snapshot schema](../src/dmux/schemas/snapshot-v2.schema.json)
and [home schema](../src/dmux/schemas/home-v2.schema.json) define version 2.
They are also available through `importlib.resources.files("dmux")` under
`schemas/`. Consumers should check the version before parsing. Removing or
renaming a field or changing its meaning requires a new schema version.
Top-level and task fields are fixed; adapter progress, metadata, scheduler,
metric configuration, presentation and telemetry objects allow extensions.
State strings remain open to adapter-specific states.

Waiting and invalid scoped plans have the required envelope, empty task and
experiment lists, and a message. Optional aggregate/resource fields appear
when the plan can be read. Task IDs combine registration ID (in home), stable
experiment tag, and stage name; labels can change independently.

An unknown denominator is JSON `null`. Counts from incompatible units must not
be added by global consumers. Home reports stage states and observed local
PIDs, never a cross-project progress percentage. Scheduler observations are
separate: `scheduler running` does not imply a matched PID. SLURM's task
`scheduler` object includes backend, job ID, state, error, and check timestamp.

Snapshot `updated` is the observation time. While a refresh is pending, the
UI retains the previous snapshot and its original timestamp. A subprocess
failure or unavailable project must not be mistaken for completion. Poll no
faster than the dashboard's normal interval; SLURM commands have a five-second
cache. Result histories are never loaded by JSON export or global home. The
`metrics` list is configuration only, for explicitly opened detail views.

GPU telemetry can retain the last good devices after a failure, with
`stale: true` and the current `error`. Do not present these as fresh values.
`--no-gpu` clears devices and reports `error: "disabled"`.

Session-list, project-list and doctor JSON remain separate contracts. The
snapshot version selector does not change those reports or the catalog format.
