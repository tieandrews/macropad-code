# Customizing keymap, colors, usage display, and the bridge

All config files live in [`config/`](../config/) and are plain YAML -- edit them in any text editor. `macropad-bridge` re-reads `config/colors.yaml` and `config/keymap.yaml` fresh on every hook event *and* every action-key press (no restart needed -- the very next key-press/state-change picks up your edit). `config/bridge.yaml` (host/port/serial settings) and `config/usage.yaml` are only read once at startup, so **do** restart the bridge after editing those.

## `config/keymap.yaml` -- session keys, action keys, and the encoder

The MacroPad's 12 keys are numbered 0-11, left-to-right then top-to-bottom, and split into two rows of roles:

```
 0   1   2      <- "session" keys: status light + selectable via the encoder
 3   4   5
 6   7   8      <- "action" keys: send input to whichever session is selected
 9  10  11
```

### Session keys (0-5) -- `keys:`

Each entry under `keys:` maps a key index to a project:

```yaml
keys:
  0:
    label: "macropad-code"
    agent: claude               # claude | codex -- also controls what
                                 # macropad-sessions launches (see below)
    project_path: "~/projects/macropad-code"
  1:
    label: "api-server"
    agent: codex
    project_path: "~/projects/api-server"
```

When a hook fires, it reads the working directory (`cwd`) of the agent session and looks for the entry whose `project_path` is the closest ancestor of that directory -- so a session running in `~/projects/macropad-code/frontend` still matches the `~/projects/macropad-code` entry. If no entry matches, nothing lights up (the hook is a no-op).

You can hand-edit this file directly, or run `macropad-setup` again to add more keys interactively (it won't remove or touch keys you've already configured unless you overwrite them with the same index).

`project_path` accepts `~` and both absolute and relative-to-home paths. It does **not** need to exist yet at assignment time -- `macropad-setup` will warn but still save it.

Only keys 0-5 are valid session slots -- the firmware's rotary-encoder selection only ever cycles through six positions, and `macropad/sessions.py` skips (and warns about) any `keys:` entry at index 6 or higher.

### The rotary encoder -- selecting a session, showing its model, voice input

Rotating the encoder cycles which of keys 0-5 is the "selected" session; that key's LED is simply rendered *brighter* than its normal status color (no animation) so it's obvious at a glance which one is selected, without it being confused for a status signal. An unconfigured key (nothing ever set its LED) shows a dim static neutral color instead while selected, purely so you can tell the selection landed there. This selection state is entirely local to the firmware -- it only tells the host which key is selected (`{"selected": N}` over serial) so action-key presses and the model/label display know where to route/refresh.

**Pressing a session key selects it directly**, same as dialing the encoder all the way around to it -- e.g. you spot a key showing the `permission`/`error` pulse and just press it instead of scrolling. This is a plain press, not press-and-hold or a double-tap; session keys have no other default behavior to conflict with (unless you've given one a local macro via `KEY_ACTIONS` in `code.py`, which takes priority and skips selection entirely for that key).

Pulsing (a slow brightness breathe) is reserved separately for status colors worth actively noticing, independent of selection -- see `config/colors.yaml`'s `pulse:` field, on by default for `permission` (needs your approval) and `error`. A pulsing key breathes on top of *whatever* brightness it's currently at, so a selected-and-pulsing key breathes between dim and extra-bright, while an unselected one breathes between dim and normal -- selection and status pulsing layer on top of each other rather than competing for the same visual channel.

The OLED's third line shows the selected session's current model (e.g. `Sonnet 4.5`), auto-detected every few seconds by the bridge snapshotting that session's tmux pane and pattern-matching Claude Code's own status line -- see `macropad/sessions.py`'s `detect_model()`. This is best-effort text scraping, same caveats as `claude_pty` usage below: it can only recognize a model name that's actually visible in Claude Code's own UI wording (`Opus`/`Sonnet`/`Haiku` followed by a version number), and shows nothing if it can't find one (session not running, mid-redraw, Codex CLI which doesn't show one the same way, ...). It refreshes immediately whenever you rotate the encoder, and otherwise on `model_poll_interval_seconds` (in `config/bridge.yaml`, default 5s).

The fourth line shows the selected session's `label` from `config/keymap.yaml` (e.g. `emexams-website`), so you can always see which repo the knob is pointed at without needing to remember key positions. It updates on the same triggers as the model line (encoder rotation, plus the same poll interval as a self-heal), and is blank for an unconfigured key.

**Pressing the encoder** toggles voice-to-text: press once to start recording from this machine's microphone (the OLED's bottom line switches from `MIC: off` to `MIC: REC`), press again to stop, transcribe locally, and send the transcribed text into the selected session as if you'd typed it. See [Voice input](#voice-input-speak-instead-of-typing) below -- off by default, since it needs an extra dependency and a working microphone.

### Action keys (6-11) -- `actions:`

Each action key does one of two things to whichever session is currently selected. Left empty (`actions: {}`) by default -- nothing fires into a live agent session until you configure it:

```yaml
actions:
  6:
    label: Approve
    send_keys: "y"
  7:
    label: Deny
    send_keys: "n"
  8:
    label: Interrupt
    send_keys: "C-c"   # tmux key-name syntax, not literal text
    enter: false        # don't send a trailing Enter after this one
  9:
    label: Usage
    send_keys: "/usage"
  10:
    label: Switch model
    type: cycle_model
    models: [sonnet, opus, haiku]
```

**`send_keys` entries (the default -- `type: send_keys` can be omitted):**

- `send_keys` is passed straight to `tmux send-keys` -- most entries are just literal text (`"y"`, `"/usage"`), but tmux's key-name syntax also works for control sequences (`"C-c"` for Ctrl+C, `"Escape"`, etc).
- `enter` (default `true`) sends a trailing `Enter` keypress after `send_keys`. Set it `false` for standalone key sequences like `C-c` where you don't want a newline afterward.

**`type: cycle_model` entries:**

- Each press advances to the next name in `models:` (wraps around) and sends `/model <name>` into the selected session -- Claude Code's own command for switching models mid-session. `models:` defaults to `[sonnet, opus, haiku]` if omitted.
- The bridge tracks each session key's own position in the rotation in memory (reset on bridge restart) -- it doesn't try to read back which model you're *actually* on first, it just advances forward each press. The OLED's model line (above) reflects the real result within a few seconds regardless, so you'll always see the truth even if the rotation's internal pointer and reality briefly disagree (e.g. right after a bridge restart, or if you also ran `/model` by hand).

Pressing an action key with nothing configured for it, or while no tmux session is running for the currently selected key, is a silent no-op (logged by the bridge, never raised) -- same fire-and-forget philosophy as everywhere else in this repo.

## Wiring action keys to a live session: tmux + Remote Control

Action keys work by injecting text into a **named tmux session** -- `macropad-key0`, `macropad-key1`, ... one per session key -- rather than trying to focus the right terminal window, which has no reliable cross-platform mechanism. That means your agent sessions need to actually run inside these tmux sessions.

`uv run macropad-sessions` automates this: for every entry in `keymap.yaml`'s `keys:` (0-5), it creates the tmux session if it doesn't already exist and launches the configured agent inside it --- `claude --remote-control "<label>"` for `agent: claude`, or a plain `codex` for `agent: codex`. Run it once after editing your keymap, or any time you want to make sure all your session tmux panes are up.

- **Prerequisite**: `tmux` must be installed (`sudo apt install tmux`, `brew install tmux`; on Windows, run this from WSL -- tmux itself doesn't exist natively on Windows).
- **`claude --remote-control`** additionally lets you review or steer that session from `claude.ai/code` or the Claude mobile app -- see [Claude Code's Remote Control docs](https://code.claude.com/docs/en/remote-control). It requires a Pro/Max/Team/Enterprise plan (not an API key) and being logged in via `/login`; Codex CLI has no equivalent yet.
- To manually check on or type into a session yourself (rather than through action keys), attach to its tmux pane directly: `uv run macropad-sessions --attach 0` (swap `0` for the key index), or plain `tmux attach -t macropad-key0`. Detach with the usual `Ctrl-b d` without killing the session.
- If an action key press logs `failed (no tmux session running for the selected key?)`, that key's tmux session either was never started (run `macropad-sessions`) or its `claude`/`codex` process has exited -- `tmux attach -t macropad-key<N>` to see why.

## Voice input: speak instead of typing

Pressing the rotary encoder toggles recording from this machine's default microphone; pressing it again stops recording, transcribes locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (an offline, CPU-friendly Whisper implementation -- no API key, no cloud, nothing leaves your machine), and sends the resulting text into whichever session is currently selected, exactly like an action key would.

**Off by default.** To turn it on:

1. Install the optional dependencies: `uv sync --extra voice` (adds `sounddevice`, `numpy`, `faster-whisper`).
2. Install two system-level packages `sounddevice` needs at runtime (Debian/Ubuntu/WSL -- other platforms typically bundle equivalents already):
   ```bash
   sudo apt install libportaudio2 libasound2-plugins
   ```
   `libportaudio2` is the audio I/O library itself; `libasound2-plugins` is the ALSA-to-PulseAudio bridge plugin -- **on WSL specifically, this second one is what actually lets ALSA see WSLg's forwarded microphone at all.** Without it, `sounddevice.query_devices()` returns an empty list even though the mic genuinely works (verify with `pactl list sources short` -- if that shows an `RDPSource` line, your mic is reachable at the PulseAudio level and it's just ALSA that can't see it yet).
3. Make sure a microphone is actually reachable from wherever the bridge runs: `uv run python -c "import sounddevice as sd; print(sd.query_devices())"` should list a `pulse` device (on WSL) with a non-zero input channel count, and `sd.default.device` shouldn't be `[-1, -1]`. If it's still empty after installing both packages above, check Windows' mic privacy settings (Settings -> Privacy & security -> Microphone -> "Let desktop apps access your microphone"), or restart WSL (`wsl --shutdown` from PowerShell, then reopen your terminal).
4. Set `voice.enabled: true` in `config/bridge.yaml` (see below) and restart the bridge.

Notes:

- The first press after a bridge (re)start is slower than the rest, since the Whisper model (a few hundred MB for the default `base` size) is downloaded once and cached under `~/.cache`, then loaded into memory and kept there for the life of the process.
- `whisper_model` in `config/bridge.yaml` trades accuracy for speed: `tiny` is fastest/least accurate, `base` (default) is a reasonable middle ground, `small`/`medium`/`large-v3` are progressively slower and more accurate on CPU.
- Every step degrades cleanly rather than crashing the bridge: no microphone, missing dependencies, a failed transcription, or empty audio all just log a message and no-op -- same fire-and-forget philosophy as everything else in this repo. See `macropad/voice.py`.
- This is a toggle, not push-to-talk -- press once to start, speak, press again when you're done. The OLED's bottom line shows `MIC: REC` the whole time so it's obvious the pad is listening, and `MIC: off` otherwise -- on its own line rather than overlaying the model name, so it stays unambiguous.
- `macropad/voice.py` deliberately records using PortAudio's *blocking* API (a plain `.read()` loop on a background thread) rather than its callback API. The callback API spawns a dedicated real-time PortAudio thread that has a [known race-condition bug](https://github.com/PortAudio/portaudio/issues/1011) (`paTimedOut` / `Error starting stream: Wait timed out`) that we hit reliably under WSL -- if you ever see that error while modifying this module, that's why.

## `config/colors.yaml` -- what each state looks like

```yaml
brightness: 0.3   # global scale, 0.0-1.0, applied to every color below

states:
  working:
    color: [255, 140, 0]
    description: Agent is actively thinking / running.
  waiting:
    color: [255, 0, 255]
    description: Agent is waiting on your next input.
  permission:
    color: [255, 0, 0]
    pulse: true
    description: Agent needs your approval before it can continue.
  # ...
```

- `color` is `[R, G, B]`, each `0-255`.
- `pulse: true` (optional, default `false`) makes that key breathe brightness instead of showing a flat color -- reserved for states genuinely worth interrupting you for (ships on `permission` and `error`, off for `waiting`/`working`/`done`/`idle`). This is independent of the encoder selection, which is always a plain brightness boost, never an animation -- see [The rotary encoder](#the-rotary-encoder----selecting-a-session-showing-its-model-voice-input) above for how the two combine on a key that's both selected and pulsing.
- `brightness` is a single global multiplier -- turn it down if the pad is uncomfortably bright next to a monitor, rather than editing every color individually.
- To add a brand-new state, add it here *and* map an event to it in `macropad/hooks/*.py` (see [agent-integration.md](agent-integration.md)) -- a state with no color entry falls back to `idle`'s color.

Changes take effect on the next hook event -- no bridge restart needed, since `color_for_state()` reads `colors.yaml` fresh every time it's called. The catch: a key that's already lit won't repaint on its own just because you edited the file -- nothing re-sends its color until *some* new state event fires for that key. To preview a change immediately without waiting for a real agent event, send a fake one by hand:

```bash
uv run python -c "from macropad.client import send_state; send_state(0, 'working')"
```

(swap `0` for the key index and `working` for any state name in `colors.yaml`).

## `config/bridge.yaml` -- daemon settings

```yaml
host: "127.0.0.1"
port: 9999

serial:
  baudrate: 115200
  retry_seconds: 2.0

voice:
  enabled: false
  whisper_model: base

model_poll_interval_seconds: 5
```

- `host`/`port`: the local socket hook scripts use to talk to the bridge. Only change this if `9999` is already taken by something else on your machine -- and if you do, restart the bridge.
- `serial.retry_seconds`: how often the bridge tries to (re)find the MacroPad if it's unplugged, asleep, or not yet connected. Lower it if you want faster reconnects at the cost of slightly more CPU/USB polling.
- `voice.enabled`/`voice.whisper_model`: see [Voice input](#voice-input-speak-instead-of-typing) above.
- `model_poll_interval_seconds`: how often the bridge re-checks the selected session's tmux pane to update the OLED's model line. Cheap (just a tmux pane snapshot), so the default (5s) is fine to leave alone.

## `config/usage.yaml` -- OLED usage dashboard

There is no official Anthropic API for your exact Claude subscription session/weekly quota -- see the note in `config/usage.yaml` itself. This repo gives you three ways to populate the OLED dashboard, selected with `source:`, in increasing order of accuracy *and* fragility/overhead. Whatever you pick, the bridge always falls back to `local_estimate` if the chosen source comes up empty -- the OLED never just goes blank because a fancier source failed.

### `source: local_estimate` (default)

```yaml
enabled: true
source: local_estimate
poll_interval_seconds: 60
session_window_hours: 5
weekly_window_days: 7
session_token_budget: null
weekly_token_budget: null
```

The bridge scans Claude Code's own local session transcripts under `~/.claude/projects/**/*.jsonl` and sums the token usage recorded in them over two rolling windows: the last `session_window_hours` and the last `weekly_window_days`. Always available, zero extra setup, Claude Code only (Codex CLI doesn't write anything locally this can read).

Anthropic doesn't publish the exact token budget behind Claude Code's real "session"/"weekly" limits, so by default `session_token_budget`/`weekly_token_budget` are `null` and the OLED shows a raw count (`128.4K`, `1.9M`, ...) rather than a fabricated percentage. If you've empirically learned roughly where your own plan's limits kick in, set either budget to your own estimated token ceiling and that metric switches to a percentage bar instead:

```yaml
session_token_budget: 500000
weekly_token_budget: 2000000
```

Other notes:

- `poll_interval_seconds` trades freshness for disk I/O -- the scan reads every transcript file modified within the weekly window on every tick. 60s is a reasonable default; raise it if you have a very large `~/.claude/projects/` history.
- The transcript JSONL format is internal to Claude Code and can change between releases. `macropad/usage.py` parses it defensively (any line, file, or field it doesn't recognize is skipped, not raised) so a format change degrades to "shows 0" rather than crashing the bridge -- but the numbers could in principle go stale if Anthropic changes the schema. If that happens, check the field names `macropad/usage.py` looks for (`message.usage.{input,output,cache_creation_input,cache_read_input}_tokens` and a top-level `timestamp`) against a real file in `~/.claude/projects/` to see what changed.

### `source: claude_monitor`

```yaml
source: claude_monitor
claude_monitor:
  state_path: null   # defaults to ~/.claude-monitor/state/latest.json
```

Reads Anthropic's *real* session/weekly percentages from the state file written by the community [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) tool -- if you already run it. We don't maintain that tool and haven't verified its output schema against a live install, so `macropad/usage_monitor.py` tries a few plausible key names (`session_percent_used`, `session_pct`, nested `session.percent_used`, ...) and returns nothing if none match, which falls back to `local_estimate`. If you run the monitor and this never picks up real numbers, open the state file yourself and check what `macropad/usage_monitor.py`'s `_SESSION_KEYS`/`_WEEKLY_KEYS` are actually looking for.

### `source: claude_pty` (experimental)

```yaml
source: claude_pty
poll_interval_seconds: 600   # keep this long -- see below
claude_pty:
  claude_command: "claude"  # use an absolute path (`which claude`) if the
                             # bridge runs as a systemd --user service
  working_dir: null         # defaults to your home directory -- must
                             # already be trusted by Claude Code (see below)
  timeout_seconds: 20
```

Gets Anthropic's real percentages by periodically launching `claude` itself in a pseudo-terminal, sending it `/usage`, and reading the panel back -- the only source that shows the actual number Claude Code shows you, at real cost:

- **It launches a full `claude` process every poll.** That's real CPU/memory/startup overhead. `macropad-setup` sets `poll_interval_seconds` to 600 when you pick this source specifically because of that -- don't run it every 60s.
- **It needs `claude` already fully onboarded and the target directory already trusted.** Launching `claude` fresh into a directory it hasn't seen, or before you've completed the theme/login wizard once, lands on that wizard or a trust prompt -- not a ready session -- and there is no way to click through a trust prompt from an unattended script without defeating the point of it. So instead of guessing, `macropad/usage_pty.py` watches for those exact screens (`"Select login method"`, `"Choose the text style"`, `"trust the files"`, ...) and bails out cleanly the moment it sees one, rather than hang or misread wizard text as usage data. **Before enabling this**, run `claude` by hand once from `claude_pty.working_dir` and get all the way to a normal chat prompt -- note this is a *per-directory* trust setting (`~/.claude.json`'s `projects` map), so trusting `~/some-project` doesn't trust `~` itself; point `working_dir` at whichever one you actually ran `claude` in.
- **`claude_command` needs to be resolvable from wherever the bridge runs.** If `macropad-bridge` runs as a `systemd --user` service (see [hardware-setup.md](hardware-setup.md)), it inherits a minimal `PATH` that typically excludes tool-manager install dirs (linuxbrew, nvm, asdf, ...). A bare `"claude"` then fails to spawn -- silently, since `get_usage_percentages()` never raises -- and this source falls back to `local_estimate` on every single poll with no error in the logs. Run `which claude` and use the absolute path if you see percentages never showing up despite everything else being right.
- **It scrapes rendered terminal text, not a documented API.** `/usage`'s output is a human-facing UI that can change wording or layout in any Claude Code release. We've verified the extraction against a real, logged-in `/usage` panel (Claude Code v2.1.212) -- the regexes in `macropad/usage_pty.py` match "Current session ... N% used" and "Current week (all models) ... N% used" -- but if wording changes in a future release, this source silently returns nothing (never garbage, never a hang) and the bridge falls back to `local_estimate`.
- **Needs the optional `pyte` dependency**: `uv sync --extra pty` (or `pip install pyte`). Without it, a cruder regex-based ANSI stripper is used, which is more likely to leave stray control-sequence noise in the extracted text.

If a poll ever seems to hang, it can't -- `timeout_seconds` (split 40/60 between the initial wait and the post-`/usage` wait) bounds the whole cycle, and the child `claude` process is always killed (`SIGTERM` then `SIGKILL`) in a `finally` block even on an exception.

### If you're on pay-as-you-go API billing instead of a subscription

None of the above applies if you're using Claude Code with a raw `ANTHROPIC_API_KEY` rather than a Pro/Max/Team subscription -- API keys don't have a fixed "session"/"weekly" percentage at all, just metered spend. Anthropic's [Usage & Cost Admin API](https://platform.claude.com/docs/en/api/admin/usage_report/retrieve_claude_code) can report that spend over time, but it requires an *organization Admin API key* most individual users won't have, and it reports raw token/cost totals, not a percentage of anything -- it's a genuinely different feature for a different audience, so this repo doesn't wire it up. If that's your situation and you want it on the OLED anyway, `macropad/usage.py`'s `build_display_payload()` is the place to add a fourth `source`.

### Toggling and switching

Run `uv run macropad-setup` again any time to change `enabled`/`source` interactively (it only patches those specific lines in place -- your comments and other settings stay put), or hand-edit `config/usage.yaml` directly.

## Firmware-side changes

Per-key LED behavior, the OLED dashboard layout, the selection-brightness/status-pulse rendering, and how JSON messages are parsed (in both directions) all live in [`firmware/code.py`](../firmware/code.py) on the MacroPad itself, not in this repo's Python. If you change it, re-copy the file to `CIRCUITPY/code.py` (or `uv run macropad-flash`, see [troubleshooting.md](troubleshooting.md#updating-firmware-over-wslusb-write-protect-errors-reverted-files)) -- no reboot needed, CircuitPython reloads `code.py` automatically on save.

The OLED shows five lines total: two usage metrics (`5H [bar] 42%` / `7D [bar] 18%`, no header), a third line for the selected session's model name, a fourth line for the selected session's project label, and a fifth/bottom line as a compact status bar (currently just `MIC: off`/`MIC: REC`, deliberately terse to leave room for more fields later). To change how the usage bars *look* (e.g. wider bars) without touching the token-counting logic, edit the constants and `_render_bar`/`_render_metric_line`/`_apply_usage_message` near the top of `code.py`. `BAR_WIDTH` is deliberately conservative (10 chars) to leave room for the label and percentage on the same line within the OLED's 128px width at the built-in font's ~6px glyph width -- widen it carefully if you switch fonts or shorten the labels further. The third/fourth/fifth lines' rendering lives in `_apply_model_message`/`_apply_label_message`/`_apply_recording_message`.

**Gotcha if you add more OLED lines/colors**: the built-in OLED is monochrome, 1-bit-per-pixel -- `displayio` auto-converts any `label.Label(..., color=...)` you pick to plain black or white by luminance, there's no dimming/tinting. A color that "should" read as dim grey or dark red (anything with roughly less than half the luminance of white) silently converts to *black*, i.e. invisible text, not a dimmer version of it -- this is exactly what happened when the bottom status line first shipped with `0x606060`/`0xFF0000`, and it never rendered at all until switched to plain `0xFFFFFF`. Stick to `0xFFFFFF` (or another color with luminance clearly above ~50%, like the label line's `0x00CFFF`) for anything you actually want visible, and use the *text* itself, not color, to carry any on/off-style distinction.

Per-key LED rendering (both the selection brightness boost and the status pulse) happens every main-loop iteration in `_render_key_leds()`, driven by the `_key_colors`/`_key_pulsing` caches that `_apply_led_message()` fills in from each `{"key", "color", "pulse"}` message. To change the pulse animation's speed/depth or the selection brightness boost, edit `_PULSE_PERIOD_SECONDS`/`_PULSE_MIN_SCALE`/`_SELECTED_BRIGHTNESS_SCALE` near `_render_key_leds()`. `SESSION_KEY_COUNT` (how many keys the encoder cycles through) and `ACTION_KEY_START` (where action keys begin) are also constants there if you ever want a different split than 6/6 -- just remember to keep `config/keymap.yaml`'s `actions:` indices and `macropad/sessions.py`'s `SESSION_KEY_COUNT` in sync if you do.

The encoder's push-button is polled by `_poll_encoder_switch()` using the MacroPad library's built-in debouncer (`macropad.encoder_switch_debounced`) -- it sends `{"encoder_press": true}` once per physical press, with debouncing handled entirely on the board.
