# Keyboard and mouse controls

Generated from `dmux.bindings.BINDINGS`.

Press or click `? help` for controls. Click a tab to select it, an experiment row to
open details, or a stage row to inspect it. Home rows/recent tabs open details;
session rows open the selected tmux destination. Click a preference to edit/toggle it.
The wheel navigates lists/tabs and scrolls logs or comparisons. Help entries for
navigation and options are clickable; stop/removal confirmation remains typed.

Mouse reporting is enabled only in interactive terminals, using
[SGR mouse reporting](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html#h3-Extended-coordinates).
Use `--no-mouse`, `DMUX_MOUSE=false`, or `dmux settings set mouse false` to disable it.
Keyboard controls remain available; terminal/emulator shortcuts can bypass mouse
reporting for text selection. dmux does not change your tmux server settings.

## Dashboard

| Keys | Action |
| --- | --- |
| n/↓ | Next experiment tab |
| p/↑ | Previous experiment tab |
| Enter | Open experiment details |
| [ | Previous stage |
| ] | Next stage |
| a | Follow the active experiment and stage |
| t | Open tmux session browser |
| k | Review stop for selected stage; exact label required |
| K | Review stop for experiment; exact label required |
| x | Hide experiment tab; jobs keep running |
| u | Restore hidden experiment tabs |
| l | Open configured log: scroll, search and follow |
| c | Compare configured key metrics (explicit bounded reads) |
| o | Options; preferences never change experiments |
| q/Esc | Back from details; q quits dashboard |
| r | Refresh |
| ? | Show keyboard help |

## Detail

| Keys | Action |
| --- | --- |
| n/↓ | Next experiment tab |
| p/↑ | Previous experiment tab |
| [ | Previous stage |
| ] | Next stage |
| a | Follow the active experiment and stage |
| t | Open tmux session browser |
| k | Review stop for selected stage; exact label required |
| K | Review stop for experiment; exact label required |
| x | Hide experiment tab; jobs keep running |
| u | Restore hidden experiment tabs |
| m | Next metrics page when available; does not save results |
| l | Open configured log: scroll, search and follow |
| c | Compare configured key metrics (explicit bounded reads) |
| o | Options; preferences never change experiments |
| q/Esc | Back from details; q quits dashboard |
| r | Refresh |
| ? | Show keyboard help |

## Home

| Keys | Action |
| --- | --- |
| o | Options; preferences never change experiments |
| j/↓ | Next run |
| k/↑ | Previous run |
| / | Search |
| f | Cycle all / running / attention |
| Tab | Select a recent experiment |
| x | Close recent tab only |
| Enter / t | Open details / tmux |
| q | Quit home |
| r | Refresh |
| ? | Show keyboard help |

## Sessions

| Keys | Action |
| --- | --- |
| o | Options; preferences never change experiments |
| / | Search |
| j/n/↓ | Next session or pane |
| k/p/↑ | Previous session or pane |
| Enter | Open selected session or pane |
| s/Tab | Toggle sessions / panes |
| d | Review session removal; exact name required |
| q/Esc | Close browser |
| r | Refresh |
| ? | Show keyboard help |

## Log

| Keys | Action |
| --- | --- |
| j/↓ | Scroll down |
| k/↑ | Scroll up |
| Ctrl-D/U | Page down / up |
| / | Search the retained log window |
| f | Toggle follow mode |
| o | Options |
| q/Esc | Back (options save on close) |
| r | Refresh |
| ? | Show keyboard help |

## Comparison

| Keys | Action |
| --- | --- |
| j/↓ | Scroll down |
| k/↑ | Scroll up |
| s | Cycle comparison sort order |
| o | Options |
| q/Esc | Back (options save on close) |
| r | Refresh |
| ? | Show keyboard help |

## Options

| Keys | Action |
| --- | --- |
| j/↓ | Scroll down |
| k/↑ | Scroll up |
| Enter | Edit or toggle selected preference |
| ← | Previous value |
| → | Next value |
| q/Esc | Back (options save on close) |
| ? | Show keyboard help |
