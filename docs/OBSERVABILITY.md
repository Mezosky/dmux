# Following runs over time

## Full log viewer

Press `l` from a dashboard or detail view to open that stage's explicitly
configured `log` path. `j`/`k` and arrows scroll; Ctrl-D/U move a page; `/` filters
the retained lines; `f` toggles follow; `r` reads the latest window; q/Esc returns.
Press `o` for preferences. Scrolling/searching freezes the current window so a
writer cannot move it under the cursor. Follow detects truncation and rotation.

The viewer reads at most 256 KiB and retains at most `log_tail` lines. It shows
when older content was omitted. Search covers this bounded window, not the
entire file. Escape/control sequences in logs are treated as data, never terminal
commands. Monitoring and browsing never change the log.

## Stalls, events and notification hooks

```bash
dmux watch --project-root /work/project --plan-dir monitor --stall-seconds 120 --history
# An existing notifier receives one JSON object on stdin per transition:
dmux watch --project-root /work/project --plan-dir monitor --notify-cmd 'my-notifier --channel training'
```

A stall advisory requires an explicit threshold, a configured progress counter,
and an observed local running process or a separate `scheduler running` report.
The no-update age uses committed progress/file evidence and subsequent observed
counter changes. A newly matched process bounds the age by its real start time.
Stalls do not replace the stage state or imply a dead process. Partial/integrity
warnings remain separate. Metric values never drive stalls or completion.

Notification commands are argv, not shell expressions. dmux sends bounded event
JSON on stdin, with `observed_at`, `plan_dir`, `experiment`, `stage`, `state`,
`previous_state` and `kind`. Kinds include `state_changed`, `stalled`, and
`progress_resumed`. Initial observations establish a baseline without an alert.
A persistent state sends no repeated alerts. Commands run on a worker, with a
five-second timeout and a 32-event queue. Failures/dropped events are visible;
delivery is best effort and pending notifications may be lost on exit. dmux
never retries notification programs indefinitely or provides cached PIDs to them.

Desktop, Slack or pager integration belongs in the user's explicit notifier.
No built-in service connection or message sending is enabled by default.

## Observed timeline

Enable `history` in settings or on interactive watch/home. `dmux timeline`
shows the observations; `dmux timeline --json` emits a version-1 event list.
The file lives under `XDG_STATE_HOME/dmux/timeline.json`, uses a lock and atomic
updates, and retains at most 2,000 events and 512 KiB. It stores state transition
labels/timestamps, not metric samples, result contents, commands or PIDs.

“Observed complete at 06:00” means dmux observed completion then; it is not a
claim that the job ended at exactly that instant. No events are reconstructed
for periods when dmux was closed. Each invocation establishes a new baseline.
Missing directories or invalid plans never become invented completion events.
Non-interactive snapshot/report commands do not write history or run hooks.

## Resource attribution

Details show CPU percentage and RSS summed over explicitly matched local PIDs.
CPU needs two samples and may exceed 100% when multiple cores are active. First
samples, denied reads and missing matches remain unknown. Identity includes
creation time so a reused PID cannot inherit an earlier CPU reading.

GPU compute-process memory is read through a bounded
`nvidia-smi --query-compute-apps` query and attributed only to matched PIDs whose
creation time predates the reading. Malformed or `[N/A]` rows stay unavailable.
Device telemetry and process-memory errors remain separate; absent attribution
is unknown, not zero. On a SLURM login host these values describe local PIDs,
not unseen workers on compute nodes. This does not recursively claim every
process in a training tree.

## Explicit result comparison and reports

Declare one key metric label per plan:

```json
"comparison": {"metric": "Loss", "stage": "train", "sort": "latest"}
```

Each experiment must have exactly one matching configured metric source. Press
`c` to open the comparison. `s` cycles sorting by experiment/latest/window-best,
`j`/`k` scroll, and `r` requests a new bounded read of the current snapshot.
The overview and global home still avoid metric reads. Comparison is an explicit
view, analogous to opening details, and never changes progress or liveness.

```bash
dmux report --project-root /work/project --plan-dir monitor --metric Loss --stage train
dmux report --project-root /work/project --plan-dir monitor --format csv --output comparison.csv
```

Reports go to stdout unless `--output` names a new file; existing files are
never overwritten. `--sort latest|window_best` and `--descending` control order.
Numeric sorting rejects mixed units. A comparison covers at most 128 experiments,
with the existing byte/sample/read budgets per source. Missing or ambiguous
metrics and malformed samples remain visible. Best means the best **valid
sample in the bounded recent window**, using an explicit min/max goal. Without
a goal it remains unknown; it is never an all-time-best claim. Reports include
resource columns, but a one-shot report usually has no second CPU sample and
does not run GPU queries, so those values can be unknown.
