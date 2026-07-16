# How agent status becomes an LED color

```
agent lifecycle event
   -> hook command (configured by macropad-setup)
   -> macropad/hooks/claude_hook.py  or  macropad/hooks/codex_hook.py
   -> macropad/client.py  (sends {"key": N, "state": "..."} over a local TCP socket)
   -> macropad/bridge.py  (the always-running daemon; looks up the color for that
      state in config/colors.yaml, sends {"key": N, "color": [r,g,b]} over serial)
   -> firmware/code.py on the MacroPad itself
   -> macropad.pixels[N] = (r, g, b)
```

The hook scripts and the bridge never talk about "color" directly -- hooks only know about **states** (`working`, `waiting`, `permission`, `done`, `error`, `idle`). The bridge is the only thing that resolves a state to an actual RGB value, by reading `config/colors.yaml`. That's what lets you change colors without touching any Python.

## Claude Code

`macropad-setup` adds hook entries to `~/.claude/settings.json` for four events, all pointing at `macropad/hooks/claude_hook.py`:

| Claude Code hook event | `notification_type` (if `Notification`) | macropad state |
|---|---|---|
| `UserPromptSubmit` | -- | `working` |
| `Notification` | `permission_prompt` | `permission` |
| `Notification` | `idle_prompt` or `agent_needs_input` | `waiting` |
| `Stop` | -- | `done` |
| `SubagentStop` | -- | `done` |

Claude Code pipes a JSON payload to the hook's stdin on every matching event (see the [hooks docs](https://code.claude.com/docs/en/hooks)); the fields the hook script reads are `hook_event_name`, `notification_type`, and `cwd`. `cwd` is how the hook figures out *which* MacroPad key to light -- see [customization.md](customization.md) for how that mapping works.

The hook script is intentionally fire-and-forget: it never raises and always exits `0`, so a bridge that isn't running, a MacroPad that isn't plugged in, or a key with no keymap entry never blocks or interrupts your Claude Code session -- it just means nothing lights up.

## Codex CLI

`macropad-setup` adds a single line to `~/.codex/config.toml`:

```toml
notify = ["/path/to/python3", "/path/to/macropad/hooks/codex_hook.py"]
```

Codex CLI invokes this after each completed turn with a JSON payload as a single command-line argument (see [Codex's advanced config docs](https://developers.openai.com/codex/config-advanced)). Currently the only event type documented is `agent-turn-complete`, which `codex_hook.py` maps to the `done` state.

**Known limitation:** Codex's `notify` doesn't currently expose fine-grained "thinking" / "waiting on approval" events the way Claude Code's hooks do, so a Codex-assigned key will only ever show `done` (and whatever it defaults to otherwise, `idle`) -- it won't show `working` or `permission`. If Codex CLI adds richer notify events in the future, extend `EVENT_STATES` in `macropad/hooks/codex_hook.py` to take advantage of them.

## The OLED usage dashboard is a separate pipeline

Everything above is about the 12 per-key LEDs, driven by hook events. The OLED's usage dashboard is unrelated: it isn't triggered by hooks at all. Instead the bridge polls `macropad/usage.py`'s `build_display_payload()` on a timer (`config/usage.yaml`'s `poll_interval_seconds`) and pushes a `{"usage": {...}}` message straight to the MacroPad over the same serial link. `build_display_payload()` is itself a small dispatcher over three interchangeable sources (`macropad/usage.py`'s own local-transcript estimate, `macropad/usage_monitor.py`, `macropad/usage_pty.py`) selected by `config/usage.yaml`'s `source:` -- see [customization.md](customization.md) for the tradeoffs between them and [running-the-bridge.md](running-the-bridge.md) for what the bridge logs while it's running.

## Adding a new state or event

1. Add the state and its color to `config/colors.yaml` (see [customization.md](customization.md)).
2. Map whatever triggers it to that state name in `macropad/hooks/claude_hook.py` (`NOTIFICATION_STATES` / `EVENT_STATES`) or `macropad/hooks/codex_hook.py` (`EVENT_STATES`).
3. Re-run `macropad-setup` (safe to re-run -- it won't duplicate existing hook entries) or restart `macropad-bridge` if you only changed colors.
