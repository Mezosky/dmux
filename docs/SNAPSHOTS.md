# Snapshot compatibility

Every `Monitor.snapshot()` result, including waiting and invalid plans, contains
`"schema_version": 1`. The `dmux json` and `--json` scoped CLI modes emit the same
versioned object with tmux observations added. Consumers should check this field
and tolerate additional fields within version 1.

Use these canonical names in new integrations:

| Canonical field | Version 1 compatibility alias |
| --- | --- |
| `plan_dir` | `queue` |
| `experiments` | `models` |
| `tasks[].experiment` | `tasks[].model` |
| `tasks[].stage` | `tasks[].name` |

Aliases remain present throughout 0.1. Removal is planned for a version 2 snapshot
schema in dmux 0.2; this release does not remove any alias. CLI and plan-input
aliases are a separate compatibility contract and remain supported.

An unknown denominator is JSON `null`. Counts from incompatible units must not
be added by global consumers. Snapshot `updated` describes the observation time;
when a background refresh is pending, the UI retains the prior snapshot and its
original timestamp. Result metrics load only in opened details and are excluded
from snapshots and progress decisions.

Home, session-list, and doctor JSON reports have their own shapes; this snapshot
contract applies to `Monitor.snapshot()` and scoped `dmux json` output.


GPU telemetry includes an additive `stale` boolean. A failed query may retain
`gpu.devices` from the last good reading with `stale: true` and the current
failure in `gpu.error`. Those values must not be presented as a fresh reading.
Disabling telemetry clears device readings and reports `error: "disabled"`;
a successful query clears the stale marker.
