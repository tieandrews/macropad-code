# Running agent sessions (tmux)

`macropad-sessions` starts one `tmux` session per configured key in `config/keymap.yaml`'s `keys:` (0-5), each running `claude --remote-control` (or `codex`/a custom command -- see [customization.md](customization.md)) in that key's `project_path`. Action keys (6-11) route into whichever session is currently selected via `tmux send-keys` -- see [agent-integration.md](agent-integration.md) for the full pipeline.

## Starting sessions

```bash
uv run macropad-sessions
```

This is **idempotent** -- safe to re-run any time:

- Keys with no `tmux` session yet get one created and launched.
- Keys that already have a running session are left alone (their agent process and history aren't touched).
- Either way, it pushes a `waiting` (magenta) LED color for that key via the bridge, so a freshly-launched session lights up immediately instead of staying dark until its first hook event (`UserPromptSubmit`/`Stop`/etc. -- which could be minutes away if you haven't typed anything into it yet). Re-running this after a bridge or MacroPad restart re-syncs the LEDs too, since the board doesn't persist state across a reboot.

Run it once per login, or wire it into whatever already starts your dev environment (a `.bashrc` line, a systemd `--user` unit, a Windows Task Scheduler entry, etc.) -- there's no built-in autostart for it since "your shell startup" varies too much across setups.

## Checking on sessions

```bash
tmux ls                                    # see all macropad-key0..5 sessions
tmux attach -t macropad-key0               # jump into key 0's session directly
tmux capture-pane -t macropad-key0 -p      # peek at its content without attaching
```

Or the same thing via `macropad-sessions` itself, which just wraps those same commands:

```bash
uv run macropad-sessions --attach 0        # same as `tmux attach -t macropad-key0`
```

Detach from an attached session with <kbd>Ctrl</kbd>+<kbd>B</kbd> then <kbd>D</kbd> -- this leaves the session (and the agent inside it) running.

## Auto-following the encoder selection: `macropad-follow`

Rather than re-running `tmux attach -t macropad-key<N>` by hand every time you rotate the knob, attach once to the shared `macropad-follow` session and leave it open in a spare terminal or tmux pane -- it live-updates to show whichever key is currently selected, no reattaching required:

```bash
uv run macropad-sessions --follow
```

This works because `macropad-bridge` keeps `macropad-follow`'s one window re-pointed at the selected key's session via `tmux link-window` every time the encoder selection changes (`macropad/sessions.py`'s `sync_follow_session()`) -- tmux updates any attached client immediately since it's the same underlying window, not a fresh render. The `macropad-follow` session itself is created automatically (by the bridge, on startup or first selection) if it doesn't exist yet -- no separate setup step. `Ctrl+B`, `D` detaches it like any other tmux session; the sessions it's pointing at (and their agents) are completely unaffected either way, since `link-window` never kills or moves the original window, just how `macropad-follow` references it.

You still need `macropad-bridge` running for this to actually track the encoder -- if `macropad-follow` seems stuck on one project, check `journalctl --user -u macropad-bridge.service -f` (or however you're running it, see [running-the-bridge.md](running-the-bridge.md)) for `sync_follow_session` failures.

## Cleaning up / restarting from scratch

If you've changed which projects are assigned to which keys in `config/keymap.yaml`, or a session gets into a bad state, kill everything and let `macropad-sessions` rebuild it:

```bash
tmux kill-session -t macropad-follow 2>/dev/null   # see warning below -- kill this one FIRST
for i in 0 1 2 3 4 5; do tmux kill-session -t "macropad-key$i" 2>/dev/null; done
uv run macropad-sessions
```

`tmux kill-session` on a name that doesn't exist just errors harmlessly (the `2>/dev/null` hides it) -- safe to run even if only some keys are configured.

**Kill `macropad-follow` first, before any `macropad-key<N>` session.** `macropad-follow`'s window is a `link-window` reference into whichever key is (or was) currently selected, not an independent copy -- tmux only actually destroys a window's process once *every* session referencing it is gone. If you kill `macropad-key0` while `macropad-follow` still links to it, the named `macropad-key0` session disappears but its `claude` process keeps running, now reachable *only* through `macropad-follow` -- invisible to `tmux ls`, and `macropad-sessions` will then happily start a brand new `macropad-key0` alongside it, leaving the old one orphaned and consuming resources with no obvious name to find it by. Killing `macropad-follow` first avoids this -- it gets recreated automatically the next time the bridge syncs the selection anyway.

## Unconfigured keys

A key with no entry under `keys:` in `config/keymap.yaml` never gets a `tmux` session and never receives an LED color, so it stays dark while idle. If you rotate the encoder onto it, it shows a dim static neutral color (not a fake status color, and not pulsing) just so you can confirm the selection landed there -- see `firmware/code.py`'s `_render_key_leds`. That's intentional: dark-when-idle means "nothing assigned here yet," while the dim glow-when-selected means "the cursor is here, but there's no status to show." Pulsing itself is reserved for status colors worth actively noticing (`config/colors.yaml`'s `pulse:` field), never for selection.
