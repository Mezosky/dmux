# SuperGPQA extraction migration

The implementation was copied from `/home/ignacio/llm_adhd`; no source file,
running queue, result row, log, or experiment process in that repository was
moved, edited, paused, or deleted.

Copied compatibility paths:

- `scripts/monitor_supergpqa.py`
- `scripts/monitor_tmux.py`
- `tests/test_supergpqa_monitor.py`
- `tests/test_monitor_tmux.py`

The scripts now delegate to the modular `src/dmux/` package. The source suite
passed all 40 tests before extraction, and those tests remain part of dmux.

## Command migration

Old command, when launched from the research root:

```bash
python scripts/monitor_supergpqa.py --color always
```

Portable dmux command, valid from any directory:

```bash
dmux watch --adapter supergpqa \
  --project-root /home/ignacio/llm_adhd \
  --queue /home/ignacio/llm_adhd/results/supergpqa/production_v1 \
  --color always
```

Or use the compatibility script from this repository:

```bash
python /home/ignacio/dmux/scripts/monitor_supergpqa.py \
  --project-root /home/ignacio/llm_adhd \
  --queue /home/ignacio/llm_adhd/results/supergpqa/production_v1
```

## Path behavior change

Previously, relative directories inside `plan.json` were resolved against a
`ROOT` derived from the monitor script location. That breaks as soon as the
script is extracted—even if `--queue` is absolute.

`dmux.Monitor` now resolves those directories against its explicit
`project_root`. The CLI exposes this as `--project-root`; generic plans may also
declare `project_root`. A regression test changes to an unrelated working
directory while monitoring a plan containing relative output paths.

## What did not change

- Counts represent committed unique evaluations, including explicit context
  exclusions, rather than elapsed GPU time.
- Malformed, duplicate, unexpected, and partial JSONL records are flagged.
- Completion codes cannot hide incomplete measurements or missing artifacts.
- Liveness comes from matching actual processes and resolved output paths.
- No queue ETA is extrapolated from a short context-window sample.
- tmux navigation requires selection and never creates, commands, or detaches jobs.
- Quitting restores terminal settings and leaves experiments running.

The generic CLI additionally offers explicit session creation/removal and
confirmed stage/experiment stop actions. These only run in response to a user
command and are separate from monitoring and tmux navigation.
