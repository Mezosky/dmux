# Demo capture notes

These images were supplied in `dmux-screenshots.zip` and captured from commit
[`659298d`](https://github.com/Mezosky/dmux/commit/659298d) on September 9, 2026.
The supplied capture notes describe real demo runs on WSL/Linux: terminal output
was collected with `tmux capture-pane` and rendered in xterm.js at 160 columns
and 2× resolution. The interface text, counts, PIDs, and plots are captured
output, not generated artwork.

The captures predate the layout and telemetry fixes in
[`9177071`](https://github.com/Mezosky/dmux/commit/9177071). They still show short
tab labels, absolute output-preview paths, the home header's “1 projects” typo,
and “GPU unavailable” for a demo with sampling disabled. Current builds fit
panels to available rows, use relative preview labels and wider tabs, correct
the singular label, and distinguish disabled sampling from telemetry errors.
These images illustrate the workflows; they are not verification of those fixes.

| Image | Captured state |
| --- | --- |
| [Overview](experiment-overview.png) | Four completed experiments, 24/24 numeric records; audio completion is artifact-only. |
| [Global home](global-home.png) | One registered project, three running workers, one completed audio run; no recent tabs opened yet. |
| [Live progress](live-progress.png) | Overall 87/900 saved; selected Bigram LM stage 28/300 (9.3%). |
| [Live details](experiment-details-live.png) | Bigram LM 38/300 (12.7%), loss/perplexity, demo metadata, and three configured output previews. |
| [Result plots](result-plots.png) | Separate live capture at 43/300 (14.3%), with 46 recent metric samples. |
| [Unconfigured previews](experiment-details-unconfigured.png) | Completed Tiny CLIP at 8/8, using a separate plan without output previews or metrics. |
| [tmux browser](tmux-session-browser.png) | Explicitly linked `zoo-tiny_llm` session with `chat` and `experiment` windows. |

Live metrics and previews refresh independently of progress snapshots, so their
sample counts can be slightly ahead. Demo chat windows are shells; they do not
depict AI conversations. Paths and PIDs belong to the capture environment.

## Showcase artwork

[dmux-highlight.png](dmux-highlight.png) combines the completed overview and a
separate live detail capture. According to the supplied generation notes, only
the abstract navy/cyan background was image-generated. The real terminal
captures, headings, frames, and glow were composed with HTML/CSS and exported
to a static PNG. It preserves the visual effect without requiring an embedded
web page. It is not an animation or a single simultaneous dashboard state.

The repository uses the PNG export and individual screenshots. The supplied
HTML files embed duplicate image data and remain in the original local archive.
All imported PNGs are losslessly compressed; their decoded pixels are unchanged.
Screenshots remain excluded from package distributions through `MANIFEST.in`.
