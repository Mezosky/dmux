# Preferences and terminal appearance

Press `o` in home, the dashboard, details, comparison, the log viewer or the
session browser. Click a preference, or move with the wheel, `j`/`k`, or arrows. Enter toggles a choice or opens a
value editor. Text editors accept JSON values. Close with `o` or Esc to save;
changes apply to the current view immediately, and dmux also saves on exit.
An active stop/removal confirmation continues to accept its typed label: `o`
does not bypass or replace confirmation.

```bash
dmux settings list
dmux settings get theme
dmux settings set theme high-contrast
dmux settings set box_style ascii
dmux settings set banner false
dmux settings reset theme
```

The file is `settings.json` beside `projects.json`, under the absolute
`XDG_CONFIG_HOME/dmux` or `~/.config/dmux`. The bundled settings JSON Schema
rejects unknown keys and invalid values. Reads are limited to 64 KiB. Writes
use the catalog's file lock and atomic replacement, reject symlinks, and merge
only changed keys so concurrent instances preserve unrelated edits.

Precedence is **CLI flag > DMUX_* environment > file > default**. Environment
values use JSON syntax: `DMUX_GPU=false`, `DMUX_INTERVAL=5`, or
`DMUX_NOTIFY_CMD='["my-notifier"]'`. The overlay shows the source next to each
value. A CLI/environment override remains authoritative while a different file
preference is saved. Merely opening dmux or reading settings writes nothing.

| Setting | Default | Behavior |
| --- | --- | --- |
| `interval` | 2 | Watch/home polling, 0.25–3600 seconds; completed home projects back off |
| `gpu` | true | Enable host GPU queries |
| `gpu_interval` | 5 | GPU cache interval, 1–3600 seconds |
| `theme` | cyan-dark | Named palette or a six-token JSON object |
| `box_style` | unicode | `ascii` substitutes terminal box/decorative glyphs |
| `mouse` | true | Click tabs/rows/help/options and use the wheel; `--no-mouse` disables reporting |
| `key_style` | both | `letters` or `arrows` changes the help labels; both inputs still work |
| `start_view` | home | Bare `dmux` opens `home` or a local `watch` |
| `log_tail` | 500 | Full log viewer window: 20–2000 lines, also capped at 256 KiB |
| `metric_window` | 256 | Detail/comparison samples: 16–256 |
| `clock` | local | Dashboard/home timestamps and timeline use `local` or `UTC` |
| `hide_completed_after` | 0 | Home hides a completed run after this many observed seconds; zero disables |
| `notify_cmd` | [] | Explicit argv notification hook; disabled when empty |
| `tmux_socket` | empty | Default socket when a command/plan supplies none |
| `ai_cli` | [] | Default argv for explicitly created chat windows; empty opens a shell |
| `banner` | true | Wordmark and tagline; false retains the compact DMUX title |
| `stall_seconds` | 0 | Explicit no-progress threshold; zero disables stall advisories |
| `history` | false | Store bounded observed state transitions in XDG state |

Watch/home accept runtime settings as kebab-case flags, such as `--theme light`,
`--box-style ascii`, `--no-banner`, `--metric-window 64`, `--history`, and
`--stall-seconds 120`. Command-specific flags are listed by `--help`.
`--no-gpu` remains supported. `ai_cli` applies only to future explicit session or
demo creation; selecting it never sends input to an existing chat or job.

## Themes and branding

Built-in palettes are `cyan-dark`, `light`, `high-contrast`, and `monochrome`.
Custom themes define exactly `accent`, `ok`, `warning`, `error`, `muted`, and
`selected`, with Rich color names or `#RRGGBB` strings. Example:

```bash
dmux settings set theme '{"accent":"#22d3ee","ok":"#4ade80","warning":"#facc15","error":"#fb7185","muted":"#94a3b8","selected":"#0891b2"}'
```

State names and warning text remain visible in every theme. `NO_COLOR` retains
plain semantic text. ASCII mode affects decoration, not the user's log contents.
Home uses the three-row horned-face lockup beside the DMUX wordmark. The
demo launcher and interactive version output retain the small wordmark; help
uses a compact DMUX heading to leave room for controls. No animation or startup delay is
introduced. Compact terminals and `banner=false` use a short title. ASCII mode uses plain
DMUX text. Mouse reporting is released on exit, suspend, and tmux handoff.

Preferences cannot weaken typed stop confirmation or initiate experiment
control. Notification hooks are explicitly configured external programs;
dmux sends observations to them, never process-control requests.
