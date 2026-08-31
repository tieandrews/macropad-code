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

## The reverse direction: action keys steering a session

Everything above flows one way, agent -> LED. Action keys (MacroPad keys 6-11) flow the other way, MacroPad -> agent:

```
rotate the encoder, or press a session key (0-5) directly
                    -> firmware/code.py brightens the "selected" key's LED locally
                    -> sends {"selected": N} to the bridge over serial
                    -> macropad/sessions.py's sync_follow_session() re-points the
                       shared follow view at macropad-key<N> (see
                       docs/running-sessions.md) -- `tmux link-window` for the
                       tmux backend, `herdr workspace focus` for herdr

press an action key -> firmware/code.py sends {"action": N} to the bridge over serial
   -> macropad/bridge.py looks up N in config/keymap.yaml's `actions:`
   -> macropad/sessions.py sends the configured keys into the *selected*
      session's pane (macropad-key<selected>), via whichever session_backend
      config/bridge.yaml selects (tmux or herdr)
   -> whatever agent is running in that pane (started by `macropad-sessions`,
      e.g. `claude --remote-control`) receives it as if you'd typed it
```

The bridge only ever tracks the *last* `{"selected": N}` it saw -- there's no round trip back to the firmware to confirm it, so this is fire-and-forget in both directions, matching every other message in this repo. See [customization.md](customization.md#action-keys-6-11---actions) for configuring what each action key sends, and [customization.md](customization.md#wiring-action-keys-to-a-live-session-tmuxherdr--remote-control) for the session setup this depends on.

`type: cycle_model` action keys follow the same path, except `macropad/bridge.py` sends `/model <name>` (advancing through a configured rotation) instead of a literal `send_keys` string -- see [customization.md](customization.md#action-keys-6-11---actions).

## Two more reverse-direction messages: model display and voice input

Alongside `{"selected": N}` and `{"action": N}`, the board sends one more message type: `{"encoder_press": true}`, when the encoder's push-button (not its rotation) is pressed. The bridge treats this as a toggle for voice-to-text capture:

```
press the encoder -> firmware/code.py sends {"encoder_press": true} to the bridge
   -> not yet recording: macropad/voice.py opens the mic (sounddevice), bridge sends
      {"recording": true} to the board (bottom OLED line switches to "MIC: REC")
   -> already recording: mic closes, bridge sends {"recording": false} (bottom line
      back to "MIC: off"), then transcribes locally (faster-whisper) in a background
      thread and macropad/sessions.py sends the resulting text into the selected
      session, same path as an action key
```

Separately, a small poller in `macropad/bridge.py` runs on its own timer (independent of both the usage-display poller and hook events): it snapshots the *selected* session's pane, pattern-matches Claude Code's model name out of it (`macropad/sessions.py`'s `detect_model()`), and sends `{"model": "..."}` to the board whenever it changes -- also triggered immediately on `{"selected": N}` so switching sessions doesn't leave a stale model name showing. The OLED's third line always shows the model name -- `{"recording": ...}` no longer overlays it, it renders on its own bottom line instead (see above) so voice-input state is unambiguous without hiding the model.

That same poller (and the `{"selected": N}` handler) also send `{"label": "..."}` -- the selected key's `label` from `config/keymap.yaml` -- which the board renders on its own fourth OLED line, independent of the model line above it and the mic status line below it.

See [customization.md](customization.md#the-rotary-encoder----selecting-a-session-showing-its-model-voice-input) and [customization.md](customization.md#voice-input-speak-instead-of-typing) for configuring/enabling these.
