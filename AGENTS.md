# AGENTS.md — dmux engineering contract

## Purpose

dmux is a terminal workspace and monitor for ML experiments.
It observes JSON, JSONL, logs, artifacts, processes, GPUs, disks, and optionally
existing tmux panes. It never becomes part of the training runtime. It has no
dependency on a particular benchmark, model family, research repository, stage
sequence, or results layout.

## Architecture

- `src/dmux/connectors.py`: bounded read-only filesystem primitives. No domain
  meaning and no writes.
- `src/dmux/adapters/base.py`: public adapter and presentation contracts.
- `src/dmux/adapters/filesystem.py`: declarative common-layout adapter.
- `src/dmux/registry.py`: the generic filesystem adapter and optional third-party
  adapters discovered through entry points.
- `src/dmux/monitor.py`: adapter-neutral snapshot aggregation and path policy.
- `src/dmux/projects.py`: per-project code/results locations and run namespaces.
- `src/dmux/system.py`: process and GPU observation; never process control.
- `src/dmux/ui.py`: responsive Rich dashboard and experiment tabs.
- `src/dmux/experiment_view.py`: experiment details and configured output previews.
- `src/dmux/tmux.py`: explicit, validated navigation only.
- `src/dmux/sessions.py`: explicit session/window creation and confirmed removal.
- `src/dmux/session_browser.py`: searchable session/window views and removal dialog.
- `src/dmux/process_actions.py`: identity-checked, confirmed SIGTERM actions.
- `src/dmux/terminal.py`: keyboard decoding and guaranteed termios restoration.
- `src/dmux/cli.py`: dependency checks and interactive/non-interactive modes.
- `src/dmux/discovery.py`: bounded read-only file/field suggestions, not framework detection.
- `src/dmux/onboarding.py`: explicit setup wizard and exclusive new-plan creation.
- `src/dmux/diagnostics.py`: read-only connection checks and stable diagnostic JSON.
- `src/dmux/demo.py`: explicitly launched tiny demos; independent live workers.
- `examples/`: tiny heterogeneous workloads; examples never supply core defaults.

## Non-negotiable invariants

1. Polling, rendering, and closing dmux must not modify or terminate experiments.
   The user explicitly requested separate stop and session-management controls:
   session creation is allowed through explicit commands/demo launch, session
   removal through exact-name confirmation, and stage/experiment SIGTERM through
   exact-label confirmation. These are never automatic monitoring actions.
2. JSONL connectors count committed unique records. Malformed,
   duplicate, semantically duplicate, partial, and unexpected rows must not
   silently inflate progress. Unexpected records are reported separately and
   excluded from saved/accepted totals; declared skipped outcomes remain counted
   as committed records and appear in excluded totals and per-group breakdowns.
3. Keep total, experiment, and stage progress distinct. Evaluation percentages
   are never labeled as elapsed-time percentages.
4. Artifact-only stages show state, not invented percentages. Never extrapolate
   a short-window speed into a heterogeneous whole-queue ETA.
5. Process liveness comes from real processes plus resolved output paths, never
   stale JSON alone.
6. tmux navigation requires explicit user selection. Never send pane input,
   detach other clients, migrate jobs, or guess ownership from names. Creation
   launches an explicit argv in a new window only. Removal reviews the selected
   session and revalidates its server identity, numeric IDs and pane PIDs; it
   clearly states that windows/jobs/chats will close. Refuse ambiguous switching.
7. Restore alternate-screen and terminal settings on quit, exceptions, Ctrl-C,
   and before tmux handoff.
8. Never import model weights, CUDA runtimes, training frameworks, or inference
   frameworks.
9. All project-generated relative paths resolve from explicit project context,
   never from dmux's package or script location.
10. Connector reads must be bounded or incremental. Do not rescan growing logs
    or JSONL files on every refresh.
11. Stop actions validate PID, creation time, command, and descendant process
    identities; never signal dmux or its ancestors. Confirmation names the stage
    or experiment. Do not stop its parent queue implicitly or auto-escalate to
    SIGKILL. Describe a sent SIGTERM as a request, not verified termination.
12. The default Python API, CLI, and UI must work with the generic filesystem
    adapter alone. Domain-specific schemas belong in optional external adapters;
    never import them from the core, UI, package exports, or built-in registry.
13. Standalone experiments are first-class. A scheduler is optional; only warn
    about a missing scheduler when the plan explicitly declares one. Read logs
    only from configured paths rather than guessing a project-specific filename.
14. Unknown numeric totals remain `None`/JSON `null`, including aggregate totals
    when any counted stage lacks a denominator. Show saved counts without
    percentages. Artifact-only stages do not enter numeric denominators.
15. `init` may create only a new plan and its parent directory after explicit
    confirmation or `--yes`. Never overwrite/merge existing plans or write to
    experiment outputs. Dry-run and cancellation write nothing. `doctor` is
    strictly read-only and must never launch project code or external tools.
16. Live demo workers run independently of the dashboard, only in fresh demo
    output locations. Quitting never signals them or removes their files. Tests
    clean up only their own exact worker identities/private tmux server.

## Visual contract

- Keep `DMUX` as the product identity and the adapter name as context.
- Experiments are browseable through visible tabs and `n`/`p`; retain the
  overview table so users can compare pipelines at a glance.
- Enter opens detail; t opens tmux; k/K stop a stage/experiment with review;
  x hides a tab without affecting jobs, and u restores it.
- Compact terminals must preserve exact stage saved/expected values and state.
- Error/integrity states outrank decoration. Use color plus text/symbols, never
  color alone.
- Maintain the cyan/dark visual language represented by `logo.png`.

## Testing and changes

Run `pytest -q` after every behavior change. Keep integrity, progress, liveness,
and terminal regression coverage in generic fixtures. Tests must use tiny
deterministic projects in temporary directories; do not download models or datasets.
tmux integration must use an explicit private temporary socket and may only kill
the server that the test created. Never touch the user's tmux server.

Add a regression test before relaxing an integrity decision. Specialized schema
logic belongs in an adapter, reusable byte/file behavior in connectors, and all
rendering in the UI. New third-party adapter support should use the
`dmux.adapters` entry-point group.

Use experiment/stage terminology in new APIs and documentation. Preserve
documented generic-plan and CLI aliases so existing user integrations keep working.

Do not publish the package or edit/move/delete any original research repository,
monitored project, or running experiment as part of dmux development.
