# Minimal results inside an experiment

Result summaries and sparklines appear only after opening an experiment with
Enter. Neither the global home nor a project's overview reads metric sources.
The metric fields do not establish completion or change progress counts.

Configure an optional `metrics` list on the task/stage that produces the data:

```json
{
  "experiment": "baseline-seed7",
  "stage": "train",
  "directory": "runs/baseline-seed7/train",
  "metrics": [
    {"label": "Loss", "type": "jsonl", "path": "history.jsonl",
     "field": "loss", "x_field": "step", "goal": "min"},
    {"label": "Validation accuracy", "type": "jsonl", "path": "history.jsonl",
     "field": "val.accuracy", "x_field": "step", "goal": "max",
     "scale": 100, "unit": "%", "precision": 1}
  ]
}
```

The source might contain:

```jsonl
{"step": 1, "loss": 1.2, "val": {"accuracy": 0.63}}
{"step": 2, "loss": 0.9, "val": {"accuracy": 0.72}}
{"step": 3, "loss": 0.7, "val": {"accuracy": 0.78}}
```

The compact panel shows each latest value, a small sample-order sparkline, and
the visible value range. `goal` adds a best value **within the retained window**,
not a claimed all-time best. There is no inferred target, smoothing, ETA, or
automatic "improved" judgment. Leave `goal` absent for metrics such as learning
rate where neither minimum nor maximum necessarily means better.

At 80×24 the panel shows up to two metrics. Larger terminals show three; `m`
pages through additional definitions. `[`/`]` changes the stage and its results.
Previews and metadata yield space to metrics on small terminals; enlarge the
terminal for the additional panels.

## Supported sources

| Type | Contract |
| --- | --- |
| `jsonl` (default) | Newline-committed JSON objects, one per observation |
| `json` | One summary object, an array of objects, or a nested array selected by `records_field` |
| `csv` | Header plus one physical line per observation; fields are literal column names |

Paths resolve against the selected stage's directory, following the normal
project/results path rules. An explicit absolute path remains absolute.
Structured JSONL logs can use any extension, including `.log`; free-form log
messages and binary model files are not parsed as metrics.

For a latest-only JSON summary:

```json
{"label": "Accuracy", "type": "json", "path": "summary.json",
 "field": "test.accuracy", "scale": 100, "unit": "%", "precision": 1}
```

This displays a numeric result but **no invented historical curve**. dmux does
not reconstruct past losses from a repeatedly overwritten scalar. To plot a
completed run, retain its history in the output files. For JSON history:

```json
{"label": "Loss", "type": "json", "path": "results.json",
 "records_field": "training.history", "field": "loss", "x_field": "epoch", "goal": "min"}
```

For CSV:

```json
{"label": "MSE", "type": "csv", "path": "history.csv",
 "field": "train.mse", "x_field": "step", "goal": "min"}
```

Here `train.mse` is the literal CSV column heading. JSON/JSONL instead use dotted
nested field paths. Multiline quoted CSV records are not supported as a metric
history contract.

## Field options

- Required: `label`, `path`, `field`.
- `type`: `jsonl`, `json`, `csv`, or `whitespace` (with explicit `columns`).
- `x_field`: optional numeric step/epoch identity. Duplicate or decreasing
  coordinates are excluded and flagged. Without it, points use file order and
  no step-level deduplication is claimed. Use separate series/files for splits
  or restarts rather than mixing repeated step identities.
- `records_field`: dotted path to a JSON array of observation objects.
- `goal`: `min` or `max`; omit when no optimization direction is appropriate.
- `scale`: explicit positive multiplier, default 1. Use 100 only for fractions
  you want displayed as percentages; do not multiply an already-percent value.
- `unit`: short display suffix, e.g. `%`, `ms`, or `tok/s`.
- `precision`: decimal places, 0–8, default 4. Extreme values use compact
  scientific notation so they fit the terminal.

Up to 12 definitions per stage are allowed. The setup wizard can optionally add
numeric fields from its chosen JSON/JSONL progress source:

```bash
dmux init --format json --source progress.json \
  --metric-field training.loss --metric-field validation.accuracy --register
```

Edit the plan to select other files, a history array, units, or optimization
goals. No project-code import, framework integration, or additional runtime
dependency is needed.

## Integrity and read limits

The reader retains at most 256 records per source and caches at most eight
sources while browsing details. JSON is capped at 256 KiB; larger documents are
reported as too large instead of partially parsed. JSONL/CSV use at most a
256 KiB tail and only newline-committed records. The same bounds apply to
headerless whitespace tables. CSV headers are capped at
16 KiB. Parsed data is reused across metrics for an unchanged source.

On file changes, the bounded window is reread; rotation/replacement/truncation
discards the old window. Missing/unreadable sources do not show cached values
as current. Non-finite, missing, malformed, duplicate, and out-of-order points
are reported rather than silently plotted. Charts may downsample to a few
dozen characters while preserving each bucket's extrema. The horizontal axis
is observation order, **not evenly spaced wall time or steps**.

These are lightweight recent-window summaries, not a replacement for detailed
scientific plotting or a full-history audit. `dmux doctor` validates the metric
configuration but does not read result histories; source/value errors appear
when the experiment is opened.

The demos exercise JSONL loss/perplexity and accuracy histories, CSV MSE, and
a latest-only JSON audio accuracy result. Their calculations are deterministic
and small; no pretrained models or external projects are needed for validation.

## Headerless whitespace tables

For numeric text histories, set `type: "whitespace"` and declare 1–32 unique
`columns` in file order. `field` and an optional `x_field` must name those columns.
Column names are literal, as with CSV. Each nonblank committed line must have
exactly the declared number of whitespace-separated values; mismatches are
reported and excluded. There is no quoting or multiline-field interpretation.

```json
{"label": "Value", "type": "whitespace", "path": "measurements.txt",
 "columns": ["timestamp", "value", "step"], "field": "value", "x_field": "step"}
```

The reader retains append order. Repeated/backward `x_field` values are excluded
with a warning; omit `x_field` when repeated coordinates are intentional. No
result source supplies progress counts. See [local tracker recipes](TRACKERS.md)
for an MLflow file-store example.
