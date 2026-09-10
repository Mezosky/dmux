# Adapter extension contract

Implement `dmux.ExperimentAdapter` or extend `dmux.FilesystemAdapter` in a
separate Python package, registering a factory in `dmux.adapters` entry points.
Core imports do not load your adapter or its dependencies. `dmux adapters`
reports whether installed factories can load; runtime connection failures still
belong in the snapshot's visible warnings. Lazy optional dependencies should
give an installation hint when the selected mode needs them.

`configure(plan)` validates explicit configuration and builds presentation
metadata. It must not launch tools or modify files: registration also calls it.
Templates are expanded before configuration; adapters receive ordinary tasks.
Connector reads must remain bounded. `inspect_task` interprets configured data,
and `state` combines that evidence with actual process matches and integrity
checks. Only the monitor supplies observed process identities. Domain reports
cannot supply remote PIDs or prove local process liveness.

Two optional methods support batched observation without changing the existing
adapter protocol:

* `configure_observation(*, external_tools=True, background=False)` configures
  runtime observation policy. Doctor calls it with `external_tools=False`;
  interactive home requests `background=True`. Omitted methods leave existing
  filesystem-only plugins unchanged.
* `prepare_poll(tasks, *, now)` receives `(normalized_task, resolved_directory)`
  pairs once per snapshot, before `inspect_task`. Use it for bounded batches and
  caching. The normalized task still has legacy `model`/`name` keys for adapter
  compatibility. Background workers must perform observation only; they cannot
  invoke actions or mutate the UI. A completed poll must match its original
  selection before being applied.

Doctor must never execute external tools. Watch's regular refresh already runs
on its background worker; initial and non-interactive snapshots are synchronous.
Interactive home invokes adapters on its UI thread, so `background=True` must
return cached/pending observations promptly. Bound every command's output and
duration. Never submit, cancel, signal or infer ownership as part of polling.

`dmux_slurm` is an example of a command-backed adapter; `dmux_mlflow` shows
domain metadata built on generic bounded file/SQLite readers. The optional
`scheduler` task field carries report provenance and never enters PID matching.
Do not put metric history in snapshots: configured metrics remain lazy inside
opened detail views.
