# Demo capture notes

These images were supplied in `dmux-2026-09-10.zip` and captured from dmux 0.2.0,
commit [`e1d7fa3`](https://github.com/Mezosky/dmux/commit/e1d7fa3), on September 10,
2026. The supplied notes describe ten real terminal captures from private tmux
panes, replayed in xterm.js at 140 columns and 2× resolution, with height varied
by view. The interface text, counts, PIDs, and plots were not retouched.

Code and demos ran under `/tmp/dmux/work`; visible paths belong to that capture
environment. Output previews display relative filenames. GPU sampling was
disabled. The live demo used `--keep-running`: ordinary watch/home leaves workers
running, while closing the default live demo launcher cleans up its own workers
and sessions.

| Image | Captured state |
| --- | --- |
| [Overview](experiment-overview.png) | Four completed experiments, 24/24 numeric records; audio completion is artifact-only. |
| [Global home](global-home.png) | One registered project, three running workers, one completed audio run; a recent Bigram LM tab. |
| [Live progress](live-progress.png) | Overall 84/900 saved; selected Bigram LM stage 28/300 (9.3%). |
| [Live details](experiment-details-live.png) | Bigram LM 30/300 (10.0%), loss/perplexity, matched-process resources, metadata, relative output previews and recent logs. |
| [Result plots](result-plots.png) | Separate live capture at 34/300 (11.3%), with 34 recent metric samples, resources, metadata and output filenames. |
| [Unconfigured previews](experiment-details-unconfigured.png) | Completed Tiny CLIP at 8/8, using a separate plan without output previews or metrics. |
| [tmux browser](tmux-session-browser.png) | Explicitly linked `demo-tiny_llm` session with `chat` and `experiment` windows. |
| [Log viewer](log-viewer.png) | Bigram LM training log in follow mode, with search and a bounded 37-line retained window. |
| [Options](preferences.png) | Theme, logo, mouse, refresh and other preferences, with their CLI/default sources. |
| [Run comparison](run-comparison.png) | Loss from completed 8-epoch and 64-epoch Bigram LM demo runs, sorted by latest loss. |

Live views were captured at different moments. Metrics and previews refresh
independently of progress snapshots, so their
sample counts can be slightly ahead. Demo chat windows are shells; they do not
depict AI conversations. Paths and PIDs belong to the capture environment.

## Showcase artwork

[dmux-highlight.png](dmux-highlight.png) combines the completed overview and a
separate live detail excerpt. According to the supplied notes, typography,
background and framing were composed with HTML/CSS; no UI content was
AI-generated. It is a static PNG, not a single simultaneous dashboard state.

The repository uses the PNG export and individual screenshots. The supplied
HTML files embed duplicate image data and remain in the original local archive.
All eleven PNGs are copied byte-for-byte from the supplied archive. Their SHA-256
hashes and dimensions match [the supplied manifest](manifest.json); its file paths
are relative to the repository root.
Screenshots remain excluded from package distributions through `MANIFEST.in`.
