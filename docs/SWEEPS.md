# Follow a configured sweep

Use `task_templates` when your runs share a layout. A template expands only
inside the configured project's results directory. Copy
[the example plan](../examples/sweep-plan.json) into a new `monitor/plan.json`
and set its project root, glob, and connector fields to your real outputs.

```json
{
  "project_root": "/work/project",
  "results_dir": "outputs",
  "tasks": [],
  "task_templates": [{
    "glob": "sweep/*",
    "tag_prefix": "run",
    "max_matches": 100,
    "max_scan": 1000,
    "task": {
      "stage": "train",
      "progress": {"type": "json", "path": "progress.json"}
    }
  }]
}
```

Here `outputs/sweep/seed-1` becomes experiment `run/sweep/seed-1`, with its
stage directory set to that matched path. Connector, log, metric and job-ID
paths then resolve relative to that directory. The default JSON counter fields
are `current` and optional `total`; configure explicit fields if yours differ.
Totals stay unknown when the workload provides none.

The `tasks` list remains required and can contain additional explicit stages.
Use two templates with the same glob and prefix but different stages to group
several stages under each run. Set `task.project` for a named project; its
results directory becomes the glob root and its namespace prefixes identities.
Templates own `directory` and experiment identity, so neither can be overridden
inside `task`. Changing the display label never changes a directory's identity.

Expansion repeats on refresh and sorts paths for stable ordering. New run
directories appear automatically; removed directories leave the current view.
No run data or plan file is written. Repeated executions need distinct output
directories. Duplicate canonical experiment/stage identities are rejected.

Globs allow 1–8 relative path segments, with `*`, `?` and character classes.
Absolute paths, `..`, recursive `**`, and symlink directories are rejected or
excluded. Hidden directories require an explicitly dot-prefixed segment.
At most 16 templates are allowed. Each has a default limit of 100 matches and
1,000 visited directory entries, including non-matching entries. Configurable
hard ceilings are 1,000 matches and 10,000 visited entries per template.
Exceeding a limit makes the plan visibly invalid; it never silently hides runs.
No matches produces an empty view, and `doctor` reports no configured stages.

```bash
dmux doctor --project-root /work/project --plan-dir monitor
dmux watch --project-root /work/project --plan-dir monitor
```

The plan's JSON Schema checks template structure even before directories exist.
Expanded tasks also undergo the adapter's semantic validation. Tracker-specific
keys stay in their adapter; templates do not detect frameworks or infer totals.
