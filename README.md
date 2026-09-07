<p align="center">
  <img src="logo.png" alt="dmux logo" width="220">
</p>

# dmux

`dmux` is a terminal workspace and monitor for machine-learning experiments.
It connects to files and processes an experiment already produces; it does not
require the training code to import dmux, and closing the dashboard leaves jobs
running.

The interface combines visual experiment tabs, a pipeline overview, exact
stage counters, responsive detail panels, resource telemetry, recent logs, and
optional tmux workspaces for experiments and AI CLI chats.

## Install

```bash
python -m pip install -e '.[dev]'
```

Runtime dependencies are `rich` and `psutil`. `tmux` and `nvidia-smi` are
optional external tools; no model weights or inference frameworks are imported.

## Try the heterogeneous model-zoo demo

Run four real, tiny, dependency-free workloads: a CLIP-like vision-language
matcher, a bigram language model, a tabular linear regressor, and an audio
frequency classifier. They emit JSONL metrics, rewritten JSON status,
checkpoint files, logs, and completion artifacts:

```bash
dmux demo --project-root /tmp/dmux-demo --tmux
dmux watch --project-root /tmp/dmux-demo --queue monitor
```

Each experiment gets its own tmux session, with `chat` and `experiment` windows.
Start your preferred AI CLI in the chat window. The worker window keeps its
output and opens a shell when the demo finishes. The dashboard reads the session
links automatically. Omit `--tmux` to run the demo without tmux; use a fresh demo
directory for each run. These are tiny deterministic fixtures; the vision/text
matcher illustrates a dual-encoder workflow without loading pretrained CLIP.

| Key | Action |
| --- | --- |
| `n` / `p` | Select an experiment tab |
| Enter | Open experiment details: stages, PIDs, metadata, outputs |
| `[` / `]` | Select a stage |
| `t` | Open the tmux browser |
| `k` / `K` | Stop the selected stage / whole experiment, after confirmation |
| `x` / `u` | Hide the selected tab / restore hidden tabs; jobs continue |
| Esc / `q` | Return from details; `q` on the overview closes dmux |

Non-interactive consumers can use:

```bash
dmux snapshot --adapter filesystem --project-root /tmp/dmux-demo --queue monitor
dmux json --adapter filesystem --project-root /tmp/dmux-demo --queue monitor
```

The declarative [`plan.json` schema](docs/PLAN_SCHEMA.md) supports:

- append-only JSONL with configurable identity, semantic duplicate detection,
  status sets, allowed values, and grouped progress;
- periodically rewritten JSON with dotted current/total fields;
- file/glob counts for checkpoints, shards, or result partitions;
- JSON or plain-file completion artifacts with required companion files;
- sanitized bounded log tails and optional process matching.

All relative task paths resolve against an explicit `--project-root` or the
`project_root` declared by the plan—not dmux's installation directory.
The generic `filesystem` adapter is the `dmux` command's default; specialized
adapters are always selected explicitly.

## Point a project at its outputs

Keep the monitoring plan beside your code while reading an external result disk:

```bash
dmux watch --project-root /work/vision --queue monitor --results-dir /data/vision-results
```

Task directories in `monitor/plan.json` resolve beneath the chosen result
directory. Absolute task paths remain absolute. A plan can also declare separate
`root` and `results_dir` settings for each named project; see
[the project configuration](docs/PLAN_SCHEMA.md#multiple-projects).

Add `metadata` and `outputs` to a task to show them when you press Enter:

```json
{
  "run": "clip",
  "stage": "evaluate",
  "directory": "clip-eval",
  "metadata": {"dataset": "validation", "seed": 7},
  "outputs": ["metrics.json", "predictions.jsonl"],
  "log": "evaluate.log"
}
```

Previews read bounded text from configured files. Binary artifacts are listed
by path and size.

## tmux sessions and AI chats

Press `t` from the dashboard or run `dmux sessions` directly. Use `/` to search,
Tab to switch between sessions and windows, and Enter to open the selection.
Project and result directories appear for workspaces created by dmux.

Create a project chat workspace:

```bash
dmux sessions new vision --project-root /work/vision --results-dir /data/vision-results
dmux sessions
```

The initial `chat` window opens a shell. To launch a chosen CLI directly, append
`--` followed by its executable and arguments. The command receives
`DMUX_PROJECT_ROOT` and `DMUX_RESULTS_DIR`. Normal tmux window creation remains
available for additional independent chats.

Press `d` in the browser to remove a selected session. Review its panes, then
type the exact session name and press Enter. Removal closes every window in that
session and can terminate its jobs and chats; result files remain on disk.
Esc cancels. Scriptable equivalent:

```bash
dmux sessions remove vision --confirm vision
```

`--tmux-socket PATH` selects a server for `demo`, `sessions`, or `watch`. Inside a
tmux session, removing the browser's own session is refused; open the browser
outside that session first. Session commands use numeric IDs and direct argument
execution as documented by the [tmux manual](https://man.openbsd.org/tmux).

Inside tmux, dmux switches only a uniquely identified client. Outside tmux, it
attaches and returns after detachment. Terminal settings are restored before
handoff and on every exit path.

## Stop a stage or experiment

`k` targets the selected stage; `K` targets all currently live stages of the
experiment. The confirmation shows PIDs and child processes. dmux verifies their
start times and commands before requesting SIGTERM. It never escalates to a force
kill automatically. To do the same from a command:

```bash
dmux kill --project-root /work/vision --queue monitor --model clip --stage evaluate
```

Omit `--stage` to stop all live stages in the experiment. Interactive use asks
for the exact experiment or experiment/stage label; scripts must provide it via
`--confirm`. The parent queue is not stopped and may schedule additional work.
Stopping a stage preserves its tmux chat windows and output files.

## Adapter API

The built-in filesystem adapter covers common layouts without experiment-code
changes. Specialized scientific invariants belong in an adapter implementing
`dmux.ExperimentAdapter`. Third-party packages can register one through the
`dmux.adapters` entry-point group:

```toml
[project.entry-points."dmux.adapters"]
my_lab = "my_lab.dmux_adapter:MyLabAdapter"
```

The separation is deliberate: connectors read bytes, adapters interpret them,
the monitor aggregates snapshots, and the UI renders those snapshots.
See [AGENTS.md](AGENTS.md) before changing integrity or navigation behavior.

## Development

```bash
pytest -q
python -m dmux --help
```

The suite includes the original 40 tests and tiny-model coverage for JSON,
JSONL, logs, artifacts, explicit project-root resolution, responsive visuals,
terminal restoration, and tmux integration on private temporary sockets.
