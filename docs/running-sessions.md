# Running agent sessions

`macropad-sessions` starts one session per configured key in `config/keymap.yaml`'s `keys:` (0-5), each running `claude --remote-control` (or `codex`/a custom command -- see [customization.md](customization.md)) in that key's `project_path`. Action keys (6-11) route into whichever session is currently selected -- see [agent-integration.md](agent-integration.md) for the full pipeline.

Which terminal multiplexer actually does this is a runtime choice, `config/bridge.yaml`'s `session_backend`:

- **`tmux`** (default) -- battle-tested, works everywhere tmux does.
- **`herdr`** -- [herdr](https://herdr.dev), an agent-aware multiplexer purpose-built for exactly this: it recognizes coding agents running inside its panes, and its CLI/socket API is designed for scripts to drive it, not just humans. Needs `herdr` installed and on `PATH` (`brew install herdr`, or see [herdr's install docs](https://herdr.dev/docs/install/)).

Both give you the same `macropad-sessions` commands below -- pick whichever you'd rather use day to day; everything after this section applies to either, with backend-specific notes called out where they differ.

Session naming differs slightly: tmux sessions are named `macropad-key<N>` (e.g. `macropad-key0`) -- plain and internal, since you mostly interact with them by key index. herdr workspaces are labeled `mp<N>-<label>` (e.g. `mp0-macropad-code`), folding in the key's configured `label:` from `keymap.yaml`, since herdr's own sidebar is something you look at and click through directly -- see `macropad/session_backends/herdr.py`. **Switching `session_backend` to `herdr` for the first time doesn't migrate anything already running under tmux** -- `macropad-sessions` will just start fresh herdr workspaces alongside your existing tmux sessions; clean up the old ones separately (below) once you've confirmed the new ones are working.

## Starting sessions

```bash
uv run macropad-sessions
```

This is **idempotent** -- safe to re-run any time:

- Keys with no session yet get one created and launched.
- Keys that already have a running session are left alone (their agent process and history aren't touched).
- Either way, it pushes a `waiting` (magenta) LED color for that key via the bridge, so a freshly-launched session lights up immediately instead of staying dark until its first hook event (`UserPromptSubmit`/`Stop`/etc. -- which could be minutes away if you haven't typed anything into it yet). Re-running this after a bridge or MacroPad restart re-syncs the LEDs too, since the board doesn't persist state across a reboot.

Run it once per login, or wire it into whatever already starts your dev environment (a `.bashrc` line, a systemd `--user` unit, a Windows Task Scheduler entry, etc.) -- there's no built-in autostart for it since "your shell startup" varies too much across setups.

**First run in a given `project_path` on a given machine:** Claude Code shows a one-time "Is this a project you created or one you trust?" prompt before it'll do anything, and `macropad-sessions` has no way to answer it for you (there's nobody "there" -- it's just keystrokes injected into a detached session). Until you answer it, that key's session sits stuck showing the prompt -- action keys/Remote Control silently do nothing, since there's no agent turn actually running yet. Attach once (`uv run macropad-sessions --attach <key>`) and pick "1. Yes, I trust this folder", then detach -- Claude Code remembers this per-directory, so it's a one-time step per `project_path` (moving a project to a new path, or a new machine, means doing it again there).

## Checking on sessions

```bash
uv run macropad-sessions --attach 0        # jump into key 0's session directly
```

Detach from an attached session to leave it (and the agent inside it) running:

- **tmux**: <kbd>Ctrl</kbd>+<kbd>B</kbd> then <kbd>D</kbd>. Or drive tmux directly: `tmux ls`, `tmux attach -t macropad-key0`, `tmux capture-pane -t macropad-key0 -p` (peek without attaching).
- **herdr**: <kbd>Ctrl</kbd>+<kbd>B</kbd> then <kbd>Q</kbd> (herdr's default prefix+detach binding). Or drive herdr directly: `herdr workspace list --session macropad`, `herdr session attach macropad` (all of this repo's sessions live in one named herdr session, `macropad`, kept separate from your own day-to-day herdr session/workspaces).

## Auto-following the encoder selection

Rather than re-attaching by hand every time you rotate the knob, attach once to the shared follow view and leave it open in a spare terminal or pane -- it live-updates to show whichever key is currently selected, no reattaching required:

```bash
uv run macropad-sessions --follow
```

`macropad-bridge` keeps the follow view pointed at the selected key's session every time the encoder selection changes (`macropad/sessions.py`'s `sync_follow_session()`), so an attached client updates immediately rather than needing a fresh reattach. How that's implemented differs by backend:

- **tmux**: a dedicated `macropad-follow` session whose one window is kept re-pointed via `tmux link-window` at the selected key's session -- tmux updates any attached client immediately since it's the same underlying window, not a fresh render.
- **herdr**: no separate session needed -- herdr already tracks one "focused" workspace per session and pushes focus changes live to attached clients, so this is just the `macropad` session itself, with `herdr workspace focus` called on selection change.

You still need `macropad-bridge` running for this to actually track the encoder -- if the follow view seems stuck on one project, check `journalctl --user -u macropad-bridge.service -f` (or however you're running it, see [running-the-bridge.md](running-the-bridge.md)) for `sync_follow_session` failures.

## Cleaning up / restarting from scratch

If you've changed which projects are assigned to which keys in `config/keymap.yaml`, or a session gets into a bad state, kill everything and let `macropad-sessions` rebuild it.

**tmux:**

```bash
tmux kill-session -t macropad-follow 2>/dev/null   # see warning below -- kill this one FIRST
for i in 0 1 2 3 4 5; do tmux kill-session -t "macropad-key$i" 2>/dev/null; done
uv run macropad-sessions
```

`tmux kill-session` on a name that doesn't exist just errors harmlessly (the `2>/dev/null` hides it) -- safe to run even if only some keys are configured.

**Kill `macropad-follow` first, before any `macropad-key<N>` session.** `macropad-follow`'s window is a `link-window` reference into whichever key is (or was) currently selected, not an independent copy -- tmux only actually destroys a window's process once *every* session referencing it is gone. If you kill `macropad-key0` while `macropad-follow` still links to it, the named `macropad-key0` session disappears but its `claude` process keeps running, now reachable *only* through `macropad-follow` -- invisible to `tmux ls`, and `macropad-sessions` will then happily start a brand new `macropad-key0` alongside it, leaving the old one orphaned and consuming resources with no obvious name to find it by. Killing `macropad-follow` first avoids this -- it gets recreated automatically the next time the bridge syncs the selection anyway.

**herdr:**

```bash
herdr --session macropad server stop 2>/dev/null   # stops the whole macropad session, all its workspaces included
herdr session delete macropad 2>/dev/null           # drop the stopped session's own bookkeeping too
uv run macropad-sessions
```

No separate-kill-order footgun here -- herdr's "follow" view is just the `macropad` session itself (see above), not a separate window referencing another session's process, so stopping the session tears down everything in one step.

## Unconfigured keys

A key with no entry under `keys:` in `config/keymap.yaml` never gets a session and never receives an LED color, so it stays dark while idle. If you rotate the encoder onto it, it shows a dim static neutral color (not a fake status color, and not pulsing) just so you can confirm the selection landed there -- see `firmware/code.py`'s `_render_key_leds`. That's intentional: dark-when-idle means "nothing assigned here yet," while the dim glow-when-selected means "the cursor is here, but there's no status to show." Pulsing itself is reserved for status colors worth actively noticing (`config/colors.yaml`'s `pulse:` field), never for selection.
