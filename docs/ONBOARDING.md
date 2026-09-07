# Connect a project

dmux consumes files your experiment already writes. It does not require an SDK,
launch the project, or load its models. Start from your project directory:

```bash
dmux init --results-dir /data/my-results
dmux doctor
dmux
```

The wizard samples at most 64 KiB from a candidate file and examines at most 500
directory entries, up to two levels below the results location. Discovery is a
hint, not a framework detector: confirm the source and field names. It skips
hidden entries and directory symlinks. Explicit paths can point elsewhere.
CSV/log/binary files are not automatically interpreted as progress counters.

Review the generated JSON before confirming. Setup creates only a new
`monitor/plan.json` (and its directory), never experiment outputs. Existing plans
are refused, including symlinks; choose another `--plan-dir` instead. Cancellation
or `--dry-run` writes nothing. Configuration edits after setup are deliberate
manual changes; the wizard does not merge or overwrite existing plans.

## Choose the output contract

| Format | What dmux counts | Configure |
| --- | --- | --- |
| `json` | A producer-supplied integer counter | Source file; dotted current/total fields |
| `jsonl` | Unique newline-committed records | Source file; identity fields; optional expected count |
| `files` | Matching unique files | Relative glob; optional expected count |
| `completion` | Completion state, not a percentage | A marker file such as `DONE` |

For logs alone, configure `log` and optionally process matching in the plan.
dmux can display logs but does not guess percentages from free-form messages.
For binary checkpoints, count paths or use them as completion markers; the
checkpoint contents are never deserialized.

JSONL counts records, not the largest step number. If a writer emits only every
100th training step, do not use the total training-step count as the expected
number of records. Use a JSON counter for step-based progress, or supply the
actual expected number of JSONL records. Leave unknown totals unknown.

## Unattended setup

Preview a connection without writing:

```bash
dmux init --project-root /work/vision --results-dir /data/vision/run-1 \
  --format json --source progress.json \
  --current-field training.step --total-field training.total --dry-run
```

Replace `--dry-run` with `--yes` to create the plan. Other examples:

```bash
dmux init --format jsonl --source predictions.jsonl --identity sample_id --expected 100 --yes
dmux init --format files --source 'checkpoint-*.json' --expected 10 --yes
dmux init --format completion --source DONE --yes
```

These are alternative plans, not commands to run into the same existing plan.
Each uses `outputs/` by default; pass `--results-dir` for your actual location.
Add `--log train.log` for log tails. For worker detection:

```bash
dmux init --format json --source progress.json --script train.py \
  --output-flag=--output-dir --yes
```

The worker must actually be running with a matching script basename and an
output argument resolving to the task directory. dmux does not start it for you.
It supports `--output-dir PATH` and `--output-dir=PATH`. Custom launchers or
remote jobs may need different configuration or an external adapter. A missing
PID does not prove a remote or inaccessible job is stopped.

The wizard records absolute project/results roots so you can later run from
another directory:

```bash
dmux doctor --plan-dir /work/vision/monitor
dmux --plan-dir /work/vision/monitor
```

To move the project, update those roots or supply `--project-root` and
`--results-dir`. For several experiments, stages, or projects, add tasks to the
plan using the [full schema](PLAN_SCHEMA.md).

## Diagnose a connection

```bash
dmux doctor
dmux doctor --json
```

Doctor is read-only. It checks the current files and real processes, not a
historical or statistical model-quality evaluation. It does not launch tools,
create tmux sessions, or execute project code; optional tools are checked for
availability only. Missing tmux or `nvidia-smi` is informational.

Exit codes: `0` means no warnings/errors, `1` means warnings, `2` means errors.
Each JSON check includes a level, stable code, scope, path, message, and hint.

| Finding | What to check |
| --- | --- |
| Plan missing | Run init, or supply the directory containing `plan.json` |
| Source or log missing | Check `results_dir`, task `directory`, then the relative file path; future outputs may legitimately be absent |
| Invalid counter fields | Use the integer field names doctor lists; do not supply percentages or guessed totals |
| JSON / JSONL mismatch | A `.json` extension can contain JSONL; configure the actual format |
| Duplicate/malformed/unexpected records | Check identity and allowed-value settings; flagged records must not inflate progress |
| Partial JSONL write | Wait for a newline commit; dmux excludes the unfinished row |
| No matching process | Check script, output flag, resolved destination and local visibility; completion/status files do not prove liveness |
| Unknown total | No fix required: saved counts remain useful without a percentage |

JSON documents have a 16 MiB read limit. JSONL diagnostics inspect one bounded
batch (up to 16 MiB); doctor warns if unread data remains. Open the dashboard to
catch up incrementally. It is not a full-history audit of an arbitrarily large
file. File-count scans honor the configured `max_files` limit.

## Explore without a real project

```bash
dmux demo --live
dmux demo --live --tmux
dmux demo --quick --project-root /tmp/my-fresh-demo
dmux doctor --project-root /tmp/my-fresh-demo
```

`--live` starts detached workers and opens the dashboard when stdin/stdout are
terminals. When redirected, it only prints the location, PIDs and reopen command.
The default is 300 steps with one-second pacing for numeric workloads; change
`--steps` and `--delay` for a shorter tour. The audio artifact-only demo completes
quickly. Without `--live`, demos run synchronously unless `--tmux` is supplied.

Quit with `q` to leave workers running, or use the explicit confirmed stop
controls. Demo files are retained, never automatically removed. With `--tmux`,
one session per experiment has separate chat/worker windows; these sessions
remain after completion until you explicitly remove them. Each creation uses
the chosen server; tests use private temporary sockets only.

Validation currently covers these deterministic demos and their output
contracts, not external research projects or pretrained model integrations.
