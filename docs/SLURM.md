# SLURM over SSH

Run dmux on the cluster's login host, where `squeue`, `sacct`, and your configured
result paths are available. The `dmux_slurm` package ships in the wheel and
loads only through the `slurm` adapter entry point. It requires no Python SDK.

Install this checkout on the cluster with `pipx install .`, then connect:

```bash
ssh -t user@login.cluster
dmux watch --adapter slurm --project-root /work/project --plan-dir monitor --no-gpu
```

Or open the same plan directly from your laptop:

```bash
ssh -t user@login.cluster 'dmux watch --adapter slurm --project-root /work/project --plan-dir monitor --no-gpu'
```

SSH provides the connection and authentication; dmux does not transfer results
or store SSH credentials. `--no-gpu` is appropriate on a login host. Host GPU,
disk and process observations describe the machine running dmux, not allocated
compute nodes. Shared result files still use the ordinary read-only connectors.

## Explicit jobs

In `monitor/plan.json`, identify a known job:

```json
{
  "project_root": "/work/project",
  "tasks": [{
    "experiment": "seed-1",
    "stage": "train",
    "directory": "outputs/seed-1",
    "slurm": {"job_id": "12345_7"},
    "log": "output.log"
  }]
}
```

Ordinary numeric job IDs and exact `array_job_id_task_id` values are supported.
Array ranges, job-name matching and inferred ownership are not supported.
Alternatively, use `"slurm": {"job_id_file": "job.id"}` to read a previously
saved ID from the explicit task directory. The file must contain only that ID
and optional surrounding whitespace, with a 256-byte read limit. dmux neither
submits the job nor creates its ID file.

[The sweep example](../examples/slurm-plan.json) combines this with
[bounded templates](SWEEPS.md), so one template follows existing run directories
that each contain a `job.id`. Add progress, metrics and completion connectors
only for files your workload actually produces. Totals are never inferred from
allocated time, scheduler state or array size.

```bash
dmux add /work/project --adapter slurm
dmux
dmux json --adapter slurm --project-root /work/project --plan-dir monitor --no-gpu
```

Registration preserves the adapter, including when opening details from home.

## State and limits

Each poll batches up to 128 distinct configured stage jobs. It runs `squeue`
for the explicit IDs and asks `sacct` only for IDs absent from the queue.
Polls are cached for five seconds; interactive refreshes run in background
workers. Each command has a two-second timeout and a 64-KiB output cap.
An accounting failure leaves healthy queue observations usable and reports an
error for affected jobs. Responses with duplicate IDs fail closed rather than
silently choosing one recycled job ID.

Accounting is limited to allocations in the previous seven days on the local
cluster, including duplicates. Job steps and federated clusters are excluded.
Old jobs, disabled accounting, permission failures, and incompatible command
output show `unavailable` with a warning. Fixture tests exercise these paths;
live operation still depends on the cluster's Slurm installation and policies.

| Scheduler report | dmux stage state without a matched local PID |
| --- | --- |
| RUNNING / COMPLETING | `scheduler running` |
| PENDING / CONFIGURING / SUSPENDED / REQUEUED / RESIZING | `scheduled` |
| COMPLETED | Complete only if configured integrity, count and completion checks pass |
| FAILED / CANCELLED / TIMEOUT / node or memory failure | Failed |
| Missing, unknown or unreadable state | Unavailable |

`scheduler running` is scheduler evidence, not a claim that dmux observed a
remote process. `pid` stays null without an actual local process match. Stop
controls retain their identity-checked local PID behavior; this adapter has no
`scancel`, submission, remote signaling or automatic job control.

`dmux doctor --adapter slurm ...` validates configuration and local files without
executing either scheduler command. Its “Scheduler queries disabled in doctor”
warning is deliberate. Use `dmux json --adapter slurm ...` for a scheduler probe.

Command fields follow the official [squeue](https://slurm.schedmd.com/squeue.html)
and [sacct](https://slurm.schedmd.com/sacct.html) references.
