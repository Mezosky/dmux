<p align="center">
  <img src="https://raw.githubusercontent.com/Mezosky/dmux/main/logo.png" alt="dmux logo" width="220">
</p>

# dmux

`dmux` is a terminal workspace and monitor for machine-learning experiments.
It connects to files and processes an experiment already produces; it does not
require the training code to import dmux, and closing the dashboard leaves jobs
running.

The interface combines visual experiment tabs, a pipeline overview, exact
stage counters, responsive detail panels, resource telemetry, recent logs, and
optional tmux workspaces for experiments and AI CLI chats.

The core is framework- and benchmark-independent. Experiments, stages, record
identities, and output locations come from your plan, not a bundled model roster.

![dmux overview with four completed experiments, visual tabs, and separate evaluation and training stages](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/experiment-overview.png)

Compare vision/text, language, tabular, and audio experiments in one overview.
Use `n` / `p` to select a tab, then Enter to inspect it. The audio example has
only completion artifacts, so it shows a completed stage without inventing a
saved-count percentage.

## Install

```bash
python -m pip install .
```

Supported platforms are Linux and macOS with Python 3.11 or newer. Windows is
unsupported: terminal handling and catalog locks require `termios` and `fcntl`.

Runtime dependencies are `rich`, `psutil`, and `jsonschema`. `tmux` and `nvidia-smi` are
optional external tools; no model weights or inference frameworks are imported.

## Try it in one command

```bash
dmux demo --live --register
```

This creates a fresh temporary project, starts independent demo workers, and
opens the dashboard in a terminal. The numeric workloads are paced over roughly
five minutes so there is time to explore. They finish on their own; `q` closes
the dashboard without stopping them. The command prints the project location
and a command to reopen it. `--register` also adds the demo to the global home:
after leaving the tour, run `dmux` from any directory to browse it again.
No model downloads or GPU are needed.

## One home for your experiments

```bash
dmux add /work/vision
dmux add /work/language
dmux
```

Bare `dmux` opens a global, searchable overview of explicitly registered plans,
grouped by project. Use `/` to search, `f` to filter running/attention states,
and Enter to open an experiment. Its details include optional small result plots.
Esc returns home; `t` opens that project's tmux browser. Tab selects a recently
opened experiment; `x` closes its recent tab without stopping jobs.

![dmux global home showing four experiments in one registered project, recent experiment tabs, three interrupted runs, and one completed audio run](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/global-home.png)

The `OPEN` strip contains recently opened experiments. This capture shows three
interrupted runs and one completed audio run; PID dashes mean no matching live
workers were found. Enter inspects the selected experiment without starting it.

The home screen compares stage states, never a combined percentage of unrelated
epochs, predictions, and checkpoints. Missing projects remain visible as
unavailable. Nothing is discovered by scanning your whole computer.

`dmux remove vision` only unregisters the project; jobs, sessions, plans, and
results stay intact. See [global registration and navigation](https://github.com/Mezosky/dmux/blob/main/docs/GLOBAL_HOME.md).

Existing scoped commands keep working: `dmux watch`, `dmux --plan-dir PATH`,
and `dmux json` still use a project plan. For a global snapshot use
`dmux home --once` or `dmux home --json`.

### Heterogeneous demo outputs

Run four real, tiny, dependency-free workloads: a CLIP-like vision-language
matcher, a bigram language model, a tabular linear regressor, and an audio
frequency classifier. They emit JSONL metrics, rewritten JSON status,
checkpoint files, logs, and completion artifacts:

```bash
dmux demo --live --tmux
# Or generate a completed fixture immediately:
dmux demo --quick --project-root /tmp/dmux-demo
```

Each experiment gets its own tmux session, with `chat` and `experiment` windows.
Start your preferred AI CLI in the chat window. The worker window keeps its
output and opens a shell when the demo finishes. The dashboard reads the session
links automatically. Omit `--tmux` to run without tmux. Without `--project-root`,
each invocation chooses a fresh temporary directory; supplied demo directories
must not already contain demo outputs. These are tiny deterministic fixtures; the vision/text
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
dmux snapshot --project-root /tmp/dmux-demo --plan-dir monitor
dmux json --project-root /tmp/dmux-demo --plan-dir monitor
```

### Reading a live run

![Live bigram language-model training at 193 of 1800 epochs, showing process PID, stage progress, resource telemetry, and a linked tmux session](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/live-progress.png)

- The top counter summarizes the whole plan. Here, `193 / 1,800` epochs are
  saved and `0/1` stages are complete.
- The selected experiment and its current stage have separate progress displays.
  They match in this single-stage example; `10.7%` measures saved epochs, not
  elapsed time or an ETA.
- The active-process line shows the matched PID and its elapsed runtime. Press
  `t` to browse the linked tmux workspace, or Enter to inspect the experiment.
- GPU readings describe device-wide activity, not usage attributed to this
  experiment. This tiny language model runs on CPU; the screenshot's busy GPU
  can belong to other workloads.

The yellow parent-queue warning is from an earlier build. Standalone experiments
no longer require a scheduler; dmux warns about a missing scheduler only when
one is explicitly configured.
Closing dmux leaves that process running; explicit stops require confirmation.

### Connect existing experiment files

From your ML project directory:

```bash
dmux init --results-dir /path/to/your/results --register
dmux doctor
dmux
```

The setup wizard asks which file or pattern supplies progress, suggests fields
from a bounded sample, and previews the plan before creating `monitor/plan.json`.
It never overwrites an existing plan, edits results, or launches project code.
`--register` adds the plan to the global home; `dmux watch` discovers the local
plan without registration. No training-code imports are required.
For unattended setup use `--yes`, or `--dry-run` to preview JSON without writing.

`dmux doctor` checks the resolved paths, counters, record integrity, configured
logs, and process matching. Missing future outputs are warnings, not invented
progress. Unknown totals show saved counts without percentages. See the
[setup and troubleshooting guide](https://github.com/Mezosky/dmux/blob/main/docs/ONBOARDING.md) and
[small starter plan](https://github.com/Mezosky/dmux/blob/main/examples/plan.json).

The declarative [`plan.json` schema](https://github.com/Mezosky/dmux/blob/main/docs/PLAN_SCHEMA.md) supports:

- append-only JSONL with configurable identity, semantic duplicate detection,
  status sets, allowed values, and grouped progress;
- periodically rewritten JSON with dotted current/total fields;
- file/glob counts for checkpoints, shards, or result partitions;
- JSON or plain-file completion artifacts with required companion files;
- sanitized bounded log tails and optional process matching.

All relative task paths resolve against an explicit `--project-root` or the
`project_root` declared by the plan—not dmux's installation directory.
The generic `filesystem` adapter is the default for both the CLI and Python API.
It is the only bundled adapter; optional external adapters can interpret custom
formats. See the [plan configuration guide](https://github.com/Mezosky/dmux/blob/main/docs/PLAN_SCHEMA.md) to connect an existing project.

## Point a project at its outputs

Keep the monitoring plan beside your code while reading an external result disk:

```bash
dmux watch --project-root /work/vision --plan-dir monitor --results-dir /data/vision-results
```

Task directories in `monitor/plan.json` resolve beneath the chosen result
directory. Absolute task paths remain absolute. A plan can also declare separate
`root` and `results_dir` settings for each named project; see
[the project configuration](https://github.com/Mezosky/dmux/blob/main/docs/PLAN_SCHEMA.md#multiple-projects).

![Live experiment details at 470 of 1800 epochs, with the worker PID, model and dataset metadata, and previews of progress.json and train.log](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/experiment-details-live.png)

Enter opens this detail view. Use `[` / `]` to choose a stage and Esc to return
to the overview. The running stage shows its PID, elapsed runtime, and process
command. Metadata comes from the plan; in this capture it identifies the model,
dataset, CPU device, and epoch count. The Outputs panel previews the configured
`progress.json` and `train.log` files, including perplexity and recent log lines.

`k` / `K` request a confirmed stage / experiment stop; `x` only hides the tab
and leaves jobs running.

Add `metadata` and `outputs` to a task to display experiment context and preview
generated files:

```json
{
  "experiment": "clip",
  "stage": "evaluate",
  "directory": "clip-eval",
  "metadata": {"dataset": "validation", "seed": 7},
  "outputs": ["metrics.json", "predictions.jsonl"],
  "log": "evaluate.log"
}
```

Previews read bounded text from configured files. Binary artifacts are listed
by path and size.

### Small result plots, only when opened

Use a task's optional `metrics` list to choose what interests you. For example,
with JSONL records containing `step`, `loss`, and `accuracy`:

```json
"metrics": [
  {"label": "Loss", "path": "history.jsonl", "field": "loss", "x_field": "step", "goal": "min"},
  {"label": "Accuracy", "path": "history.jsonl", "field": "accuracy", "x_field": "step",
   "goal": "max", "scale": 100, "unit": "%", "precision": 1}
]
```

Enter opens small cyan sparklines with the latest value, the visible-window
best when a goal is configured, and a value range. `m` pages through additional
metrics. Loss, accuracy, perplexity, MSE, throughput, and arbitrary numeric fields
work the same way; none are hard-coded into the core.

Metric sources are read only inside experiment details—not by the global home
or project overview. JSON summaries show a value without inventing history;
JSON arrays, JSONL, and CSV can supply historical samples. Plots use bounded,
cached windows and never change completion counts. See the
[result configuration guide](https://github.com/Mezosky/dmux/blob/main/docs/RESULTS.md) for formats, limits, and examples.

<details>
<summary>What if output previews are not configured?</summary>

![Completed experiment details showing the results directory, exact saved counts, and an unconfigured Outputs panel](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/experiment-details-unconfigured.png)

Monitoring progress does not require output previews. This completed experiment
still shows its results directory and `8 / 8` saved records; the Outputs panel
explains how to enable previews. A dash under PID means no matching live process
was found. If outputs are configured but their files do not exist yet, dmux waits
for them to appear without creating or modifying them.

</details>

## tmux sessions and AI chats

Press `t` from the dashboard or run `dmux sessions` directly. Use `/` to search,
Tab to switch between sessions and windows, and Enter to open the selection.
Project and result directories appear for workspaces created by dmux.

![tmux session browser linking the dmux-live session and its chat and experiment windows to tiny_llm, with separate project and results paths](https://raw.githubusercontent.com/Mezosky/dmux/main/screenshots/tmux-session-browser.png)

The selected `dmux-live` session contains independent `chat` and `experiment`
windows and is linked to `tiny_llm`. Use the arrow keys or `j` / `k` to select a
session; Tab lets you choose a specific window/pane before pressing Enter.
The project and results paths show which workspace you are entering. Browsing
does not create sessions or send commands into existing chats.

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
dmux kill --project-root /work/vision --plan-dir monitor --experiment clip --stage evaluate
```

Omit `--stage` to stop all live stages in the experiment. Interactive use asks
for the exact experiment or experiment/stage label; scripts must provide it via
`--confirm`. If you use a scheduler, it is not stopped and may schedule additional work.
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
See [AGENTS.md](https://github.com/Mezosky/dmux/blob/main/AGENTS.md) before changing integrity or navigation behavior.

## Development

```bash
python -m pip install -e '.[dev]'
pytest -q
python -m dmux --help
```

The suite uses generic fixtures and tiny models to cover JSON, JSONL, logs,
artifacts, explicit project-root resolution, responsive visuals, terminal
restoration, and tmux integration on private temporary sockets.
Compatibility validation uses these demos only; it does not claim certification
against arbitrary ML frameworks or external research projects.


## Options and keyboard reference

`dmux --help` lists commands; `dmux COMMAND --help` lists each command's options.
Scoped monitoring supports `--interval SECONDS` (default 2, minimum 0.25),
`--color auto|always|never`, and `--tmux-client /dev/pts/N` to select a client
explicitly when several clients share a tmux session. `--no-gpu` disables GPU
queries. `dmux adapters` lists built-in and installed entry-point adapters.
`dmux demo --tmux --session-prefix NAME` selects names for the four fresh demo
sessions. `dmux sessions --json` prints the session browser's observations.

Press `?` in the dashboard, detail, home, or session browser for keyboard help.
On the dashboard, `a` resumes following the active experiment/stage and `r`
requests a refresh. Arrow keys navigate; the literal `k`/`K` keys open stop review.
When every experiment tab is hidden, `u` restores them. The
[keyboard reference](https://github.com/Mezosky/dmux/blob/main/docs/KEYS.md) is generated
from the same binding table as in-app help. Termination signals restore the
terminal; Ctrl-Z suspends dmux after restoration, and `fg` resumes the display.

Telemetry refreshes run in a background worker, retaining the last snapshot
while a query is pending. tmux browser refreshes also run in the background;
explicit navigation and confirmed removal revalidate the selected target.
Detail previews cache unchanged file tails and rediscover output patterns every
two seconds. Closing a detail discards its preview and metric caches.

See the [versioned snapshot contract](https://github.com/Mezosky/dmux/blob/main/docs/SNAPSHOTS.md)
for machine-readable output and the 0.2 alias migration plan.

## Development checks

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check .
mypy
python -m build
```

CI runs these checks on Python 3.11–3.13, on Ubuntu and macOS. Linux-only PTY
integration tests use owned subprocesses and a private temporary tmux socket.
The package includes `py.typed`; public annotated contracts are checked with
mypy, while existing unannotated implementation bodies remain gradually typed.
Screenshots and tests stay in the repository and are excluded from distributions.

## Remote experiments

Install dmux on the machine running the experiments, connect with SSH, and run
`dmux add /absolute/project/path` followed by `dmux` there. Its registry, process
IDs, GPU queries, and tmux socket all belong to that host. Reconnect with SSH and
reopen dmux to continue monitoring; closing the dashboard leaves experiments
running. A local view of a mounted remote filesystem can show files, but it does
not establish remote process liveness or provide remote stop controls.
