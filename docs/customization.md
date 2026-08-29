# Customizing keymap, colors, usage display, and the bridge

All config files live in [`config/`](../config/) and are plain YAML -- edit them in any text editor, or use `macropad-webui` (see [Web UI](#web-ui-macropad-webui) below) if you'd rather not hand-edit YAML. `macropad-bridge` re-reads `config/colors.yaml` and `config/keymap.yaml` fresh on every hook event *and* every action-key press (no restart needed -- the very next key-press/state-change picks up your edit). `config/bridge.yaml`'s `voice`/`encoder` settings are cached at startup but can be hot-reloaded by sending `{"reload_config": true}` to the bridge's socket (the web UI's "Apply to pad" does this automatically); `host`/`port`/`serial` and `config/usage.yaml` are only read once at startup, so **do** restart the bridge after editing those directly.

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
    permission_mode: auto       # optional -- see below
  1:
    label: "api-server"
    agent: codex
    project_path: "~/projects/api-server"
```

When a hook fires, it reads the working directory (`cwd`) of the agent session and looks for the entry whose `project_path` is the closest ancestor of that directory -- so a session running in `~/projects/macropad-code/frontend` still matches the `~/projects/macropad-code` entry. If no entry matches, nothing lights up (the hook is a no-op).

You can hand-edit this file directly, run `macropad-setup` again to add more keys interactively (it won't remove or touch keys you've already configured unless you overwrite them with the same index), or use the [web UI](#web-ui-macropad-webui) -- click a session key (0-5) in its pad-layout view to edit the same fields (label, project path, agent, permission mode) visually.

`project_path` accepts `~` and both absolute and relative-to-home paths. It does **not** need to exist yet at assignment time -- `macropad-setup` will warn but still save it.

Only keys 0-5 are valid session slots -- the firmware's rotary-encoder selection only ever cycles through six positions, and `macropad/sessions.py` skips (and warns about) any `keys:` entry at index 6 or higher.

**`permission_mode`** (optional, `agent: claude` only) -- passed as Claude Code's own [`--permission-mode`](https://code.claude.com/docs/en/cli-reference) flag when `macropad-sessions` launches this key's session:

- `manual` (or omitted -- this is Claude Code's own default) prompts for approval on every action that needs it.
- `auto` lets Claude Code's built-in classifier auto-approve low-risk actions, still asking for riskier ones.
- `bypassPermissions` skips permission checks entirely ("YOLO" mode) -- only use this for a project/session you fully trust running unattended, since nothing will stop it from running any command or editing any file without asking first.

This only takes effect for a session `macropad-sessions` actually starts -- changing it for a key with an already-running tmux session has no effect until that session is stopped (`tmux kill-session -t macropad-key<N>`) and restarted.

### The rotary encoder -- selecting a session, showing its model, voice input

Rotating the encoder cycles which of keys 0-5 is the "selected" session; that key's LED is simply rendered *brighter* than its normal status color (no animation) so it's obvious at a glance which one is selected, without it being confused for a status signal. An unconfigured key (nothing ever set its LED) shows a dim static neutral color instead while selected, purely so you can tell the selection landed there. This selection state is entirely local to the firmware -- it only tells the host which key is selected (`{"selected": N}` over serial) so action-key presses and the model/label display know where to route/refresh.

**Pressing a session key selects it directly**, same as dialing the encoder all the way around to it -- e.g. you spot a key showing the `permission`/`error` pulse and just press it instead of scrolling. This is a plain press, not press-and-hold or a double-tap; session keys have no other default behavior to conflict with (unless you've given one a local macro via `KEY_ACTIONS` in `code.py`, which takes priority and skips selection entirely for that key).

Pulsing (a slow brightness breathe) is reserved separately for status colors worth actively noticing, independent of selection -- see `config/colors.yaml`'s `pulse:` field, on by default for `permission` (needs your approval) and `error`. A pulsing key breathes on top of *whatever* brightness it's currently at, so a selected-and-pulsing key breathes between dim and extra-bright, while an unselected one breathes between dim and normal -- selection and status pulsing layer on top of each other rather than competing for the same visual channel.

The OLED's third line shows the selected session's current model (e.g. `Sonnet 4.5`), auto-detected every few seconds by the bridge snapshotting that session's tmux pane and pattern-matching Claude Code's own status line -- see `macropad/sessions.py`'s `detect_model()`. This is best-effort text scraping, same caveats as `claude_pty` usage below: it can only recognize a model name that's actually visible in Claude Code's own UI wording (`Opus`/`Sonnet`/`Haiku` followed by a version number), and shows nothing if it can't find one (session not running, mid-redraw, Codex CLI which doesn't show one the same way, ...). It refreshes immediately whenever you rotate the encoder, and otherwise on `model_poll_interval_seconds` (in `config/bridge.yaml`, default 5s).

The fourth line shows the selected session's `label` from `config/keymap.yaml` (e.g. `emexams-website`), so you can always see which repo the knob is pointed at without needing to remember key positions. It updates on the same triggers as the model line (encoder rotation, plus the same poll interval as a self-heal), and is blank for an unconfigured key.

The fifth line shows that session's current git branch (e.g. `main`), via `git branch --show-current` against its `project_path` -- see `macropad/sessions.py`'s `detect_branch()`. Same refresh triggers as the model/label lines.

**Holding the encoder down** (if `voice.triggers.encoder: true` in `config/bridge.yaml`) drives voice-to-text -- same push-to-talk/tap-to-toggle gestures as an action key with `type: voice_toggle`. While recording (or awaiting a pending-review confirm tap), the OLED's fifth line gets a `*MIC* ` marker prefixed onto the branch name, e.g. `*MIC* main` -- it disappears once the mic goes idle. See [Voice input](#voice-input-speak-instead-of-typing) below.

**Rotating the encoder** normally cycles session selection, as described above. Setting `encoder.rotation_mode: effort` in `config/bridge.yaml` repurposes rotation instead: each detent cycles the *selected* session's Claude Code effort level, same as a `type: cycle_effort` action key (sending `/effort <level>`). Session keys (0-5) still jump selection directly by pressing them in either mode -- only rotation itself changes meaning. The bridge pushes whichever mode is configured to the board on connect, so switching modes just means editing `bridge.yaml` and letting the bridge reconnect (or restart it).

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
  11:
    label: Cycle effort
    type: cycle_effort
    levels: [low, medium, high, max]
```

**`send_keys` entries (the default -- `type: send_keys` can be omitted):**

- `send_keys` is passed straight to `tmux send-keys` -- most entries are just literal text (`"y"`, `"/usage"`), but tmux's key-name syntax also works for control sequences (`"C-c"` for Ctrl+C, `"Escape"`, etc).
- `enter` (default `true`) sends a trailing `Enter` keypress after `send_keys`. Set it `false` for standalone key sequences like `C-c` where you don't want a newline afterward.

**`type: cycle_model` entries:**

- Each press advances to the next name in `models:` (wraps around) and sends `/model <name>` into the selected session -- Claude Code's own command for switching models mid-session. `models:` defaults to `[sonnet, opus, haiku]` if omitted.
- The bridge tracks each session key's own position in the rotation in memory (reset on bridge restart) -- it doesn't try to read back which model you're *actually* on first, it just advances forward each press. The OLED's model line (above) reflects the real result within a few seconds regardless, so you'll always see the truth even if the rotation's internal pointer and reality briefly disagree (e.g. right after a bridge restart, or if you also ran `/model` by hand).

**`type: cycle_effort` entries:**

- Each press advances to the next name in `levels:` (wraps around) and sends `/effort <level>` into the selected session -- Claude Code's command for setting its extended-thinking effort. `levels:` defaults to `[low, medium, high, max]` if omitted.
- Unlike model name, there's no reliable way to scrape Claude Code's tmux pane for the *actual* current effort level, so the OLED's effort display (appended to the model line, e.g. `Sonnet 4.5 - high`) only reflects what this bridge itself last sent via a `cycle_effort` press or the encoder (see below) -- it resets to blank on bridge restart, and won't notice if you set effort by typing `/effort` yourself.
- The same rotation logic can also be driven by turning the encoder instead of pressing a key -- see `config/bridge.yaml`'s `encoder.rotation_mode: effort` below.

**`type: voice_toggle` entries:**

- Hold to push-to-talk, release to stop; or tap to start hands-free recording, tap again to stop -- same gestures as the encoder press. This key's LED also shows mic state (dim blue idle / bright blue recording / amber pending review). Configure `voice.backend`, `hold_threshold_ms`, `auto_enter`, and API keys in `config/bridge.yaml` / `.env` (see [Voice input](#voice-input-speak-instead-of-typing)).

**`type: resync` entries:**

- Forces every session key's (0-5) LED to match reality right now, without needing to unplug the pad or restart the bridge -- see [Resyncing LEDs](#resyncing-leds) below for why this is ever needed.

Pressing an action key with nothing configured for it, or while no tmux session is running for the currently selected key, is a silent no-op (logged by the bridge, never raised) -- same fire-and-forget philosophy as everywhere else in this repo.

## Resyncing LEDs

The MacroPad's NeoPixels have **no memory of their own on the host side** -- each key just holds whatever color it was last told, forever, until something explicitly changes it. That has one non-obvious consequence: restarting `macropad-bridge` (a code update, a crash, `Ctrl-C`) does **not** clear the board's LEDs, because the board itself never reboots -- only the bridge process (a separate program on your computer) does. If a key's last-known color came from something that's no longer true -- a session you removed from `keys:`, leftover test traffic sent straight to the bridge's socket, whatever -- it'll keep showing that stale color indefinitely, with nothing to naturally correct it.

Three ways to fix it, roughly cheapest first:

1. **Press the `type: resync` action key**, if you've configured one (see above) -- forces every session key (0-5) to `waiting` (if it has a live tmux session) or `idle` (if not), immediately. This is the easiest option and doesn't require touching a terminal.
2. **Restart `macropad-bridge`.** On startup it automatically resyncs every session key the same way the `resync` action key does (`Bridge._resync_session_leds`), and self-heals via the periodic model-poll loop if a key's push happened to race the serial connection still (re)establishing. This also fixes anything that depended on bridge-side state (e.g. `cycle_model`/`cycle_effort`'s in-memory position, or a `pending_review` voice state stuck mid-way).
3. **Unplug and replug the MacroPad** (or trigger a firmware soft-reboot via `Ctrl-D` over the serial console). This is the only option that resets `firmware/code.py`'s own `_key_colors` array from scratch -- necessary if a key is stuck showing a color that doesn't correspond to *any* current state at all (which shouldn't normally happen once `resync` exists, but is the ultimate fallback). `macropad-bridge` reconnects automatically once the board re-enumerates, no separate restart needed on the host side.

## Wiring action keys to a live session: tmux + Remote Control

Action keys work by injecting text into a **named tmux session** -- `macropad-key0`, `macropad-key1`, ... one per session key -- rather than trying to focus the right terminal window, which has no reliable cross-platform mechanism. That means your agent sessions need to actually run inside these tmux sessions.

`uv run macropad-sessions` automates this: for every entry in `keymap.yaml`'s `keys:` (0-5), it creates the tmux session if it doesn't already exist and launches the configured agent inside it --- `claude --remote-control "<label>"` for `agent: claude`, or a plain `codex` for `agent: codex`. Run it once after editing your keymap, or any time you want to make sure all your session tmux panes are up.

- **Prerequisite**: `tmux` must be installed (`sudo apt install tmux`, `brew install tmux`; on Windows, run this from WSL -- tmux itself doesn't exist natively on Windows).
- **`claude --remote-control`** additionally lets you review or steer that session from `claude.ai/code` or the Claude mobile app -- see [Claude Code's Remote Control docs](https://code.claude.com/docs/en/remote-control). It requires a Pro/Max/Team/Enterprise plan (not an API key) and being logged in via `/login`; Codex CLI has no equivalent yet.
- To manually check on or type into a session yourself (rather than through action keys), attach to its tmux pane directly: `uv run macropad-sessions --attach 0` (swap `0` for the key index), or plain `tmux attach -t macropad-key0`. Detach with the usual `Ctrl-b d` without killing the session.
- If an action key press logs `failed (no tmux session running for the selected key?)`, that key's tmux session either was never started (run `macropad-sessions`) or its `claude`/`codex` process has exited -- `tmux attach -t macropad-key<N>` to see why.

## Voice input: speak instead of typing

Hold-and-release the rotary encoder (if `voice.triggers.encoder: true`) or an action key with `type: voice_toggle` to record from this machine's default microphone, then transcribe and send the result into whichever session is currently selected -- classic push-to-talk. A quick tap instead of a hold does something slightly different: recording keeps going hands-free, and a **later** tap stops it -- useful for dictating something longer than you'd want to physically hold a button for.

**Press vs. release, in detail:**

- **Press** always starts recording immediately, whichever gesture it turns out to be.
- **Release before `voice.hold_threshold_ms`** (default 300ms -- a "tap"): recording keeps running hands-free. The *next* press on the same control stops it (tap-to-toggle).
- **Release at or after the threshold** (a "hold"): stops recording right there -- push-to-talk.
- If a transcript is sitting **pending review** (see `auto_enter` below), the next press submits it instead of starting a new recording.
- Hands-free recording auto-stops after `voice.max_recording_seconds` (default 600s/10min) even if nothing taps the mic again -- a safety net against an errant press leaving it recording indefinitely. Set to `0`/`null` to disable.

**Reviewing before it's sent** (`voice.auto_enter: false`): by default (`true`) stopping recording sends the transcript and an `Enter` immediately, same as before. Set it `false` and the transcript is typed into the session's input box *without* Enter, so you can read it, fix a misheard word, or delete it entirely, then tap the mic control again to submit (send Enter) once you're happy with it. The mic key's LED (if you've assigned one via `type: voice_toggle`) turns amber while a transcript is pending, so it's visually obvious you still need to confirm.

**Mic key LED** (only for a `type: voice_toggle` action key -- the encoder has no LED of its own): dim blue at rest (marks it as the mic control even when idle), bright blue (pulsing) while actively recording, amber while a transcript awaits your confirm tap. This overrides whatever status color that key would otherwise show.

**Off by default.** To turn it on:

1. Install dependencies: `uv sync --extra voice`
2. Install system audio packages (WSL): `sudo apt install libportaudio2 libasound2-plugins`
3. Copy `.env.example` to `.env` and set API keys for your chosen backend
4. Set `voice.enabled: true` and `voice.backend:` in `config/bridge.yaml`, restart the bridge

### STT backends (`voice.backend`)

| Backend | Type | API key | Notes |
|---|---|---|---|
| `local_whisper` | batch | none | Offline faster-whisper; slow on CPU |
| `openai_whisper` | batch | `OPENAI_API_KEY` | whisper-1 (~$0.006/min) |
| `openai_gpt4o_mini` | batch | `OPENAI_API_KEY` | gpt-4o-mini-transcribe (~$0.003/min) |
| `openai_realtime` | **streaming** | `OPENAI_API_KEY` | gpt-realtime-whisper -- text appears in the session as you speak |
| `together_whisper` | batch | `TOGETHER_API_KEY` | openai/whisper-large-v3 |
| `together_realtime` | **streaming** | `TOGETHER_API_KEY` | Together WebSocket realtime |

Streaming backends (`openai_realtime`, `together_realtime`) append transcript **deltas** into the selected tmux session as you speak (no Enter until you press stop). Batch backends wait until you stop, then send the full transcript + Enter.

**Mic feedback is instant regardless of backend.** For streaming backends, the WebSocket handshake to OpenAI/Together (a real network round-trip, easily 100ms-1s+) happens in the background *after* the mic LED/OLED already show recording and the microphone itself has started capturing -- audio captured during that handshake is buffered and sent the moment the connection lands, nothing is lost. This means pressing the mic key never waits on the network before you see/hear confirmation it's listening.

Example `config/bridge.yaml`:

```yaml
voice:
  enabled: true
  hold_threshold_ms: 300   # below this = tap-to-toggle, at/above = push-to-talk
  auto_enter: true          # false = type transcript, wait for a confirm tap
  max_recording_seconds: 600 # safety net -- auto-stops hands-free recording after this long; 0/null disables
  backend: openai_realtime
  openai:
    api_key_env: OPENAI_API_KEY
    realtime_model: gpt-realtime-whisper
    delay: medium        # minimal | low | medium | high | xhigh
    language: en
  triggers:
    encoder: true
```

Example action key (`config/keymap.yaml`):

```yaml
actions:
  8:
    label: Mic toggle
    type: voice_toggle
```

Notes:

- API keys live in `.env` (see `.env.example`) -- loaded automatically at bridge startup
- The OLED's fifth line (normally showing the current git branch) gets a `*MIC* ` marker prefixed onto it while recording or pending review
- With streaming backends (`openai_realtime`, `together_realtime`), text already appears in the session as you speak -- `auto_enter: false` only holds back the final `Enter`, not the streamed text itself
- Every step degrades cleanly -- missing keys, failed API calls, or no mic all log and no-op
- PortAudio blocking API is used (not callback) for WSL compatibility -- see `macropad/voice.py`
- The `type: voice_toggle` key's press/release timing is tracked in firmware (`firmware/code.py`'s `_poll_keys`) and interpreted by the bridge (`macropad/bridge.py`'s `_on_voice_press`/`_on_voice_release`) -- if you reflash older firmware that doesn't send `voice_press`/`voice_release`, the bridge falls back to treating each key press as an immediate toggle (see `_dispatch_action`'s `voice_toggle` branch)

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
  backend: local_whisper
  whisper_model: base
  openai:
    api_key_env: OPENAI_API_KEY
    model: gpt-4o-mini-transcribe
    realtime_model: gpt-realtime-whisper
    delay: medium
  together:
    api_key_env: TOGETHER_API_KEY
    model: openai/whisper-large-v3
  triggers:
    encoder: true

encoder:
  rotation_mode: session

model_poll_interval_seconds: 5
```

- `host`/`port`: the local socket hook scripts use to talk to the bridge. Only change this if `9999` is already taken by something else on your machine -- and if you do, restart the bridge.
- `serial.retry_seconds`: how often the bridge tries to (re)find the MacroPad if it's unplugged, asleep, or not yet connected. Lower it if you want faster reconnects at the cost of slightly more CPU/USB polling.
- `voice.*`: see [Voice input](#voice-input-speak-instead-of-typing) above -- backend, API keys (`.env`), triggers.
- `encoder.rotation_mode`: `session` (default) or `effort` -- what turning the knob does. See [Action keys](#action-keys-6-11---actions) above for the `effort` mode's behavior; `effort_levels:` under the same `encoder:` block overrides the default `[low, medium, high, max]` cycle.
- `model_poll_interval_seconds`: how often the bridge re-checks the selected session's tmux pane to update the OLED's model line. Cheap (just a tmux pane snapshot), so the default (5s) is fine to leave alone.

## Web UI: `macropad-webui`

`uv sync --extra webui && uv run macropad-webui` starts a local web UI at `http://127.0.0.1:8787` for editing `colors.yaml`/`keymap.yaml`/`bridge.yaml` without hand-writing YAML -- a visual pad layout (click a session key 0-5 to assign it to a project, or an action key 6-11 to configure its action), color pickers + pulse toggles for each status, and forms for the voice backend and encoder settings.

Click a **session key (0-5)** to set its label, project path, agent (Claude Code or Codex CLI), and [`permission_mode`](#session-keys-0-5----keys) -- the same fields `macropad-setup` asks for interactively, or that you'd hand-edit under `keys:`. Leaving the project path blank unassigns the key (its LED stays dark, same as never configuring it).

Whichever **action key (6-11)** has `type: voice_toggle` is highlighted blue in the pad layout (mirroring the physical key's blue LED), so it's obvious at a glance which key is the mic control without opening it.

**Versions, not direct edits.** The UI never writes to `config/` directly. Edits happen against named "versions" under `config-versions/<name>/` (gitignored -- these are your personal drafts, not something to commit) -- full copies of the three editable files. Create/duplicate/rename/delete versions from the version bar at the top; the first run seeds a `default` version from whatever's currently in `config/`. Nothing reaches the live pad until you click **Apply to pad**, which copies the selected version's files over `config/*.yaml` and, if `macropad-bridge` is running, sends it a reload signal so voice/encoder settings take effect immediately (colors/keymap already reload on every access, no signal needed -- see the note at the top of this doc). Changing a session key's project/agent/permission_mode doesn't restart an already-running tmux session for that key -- see the note at the end of [permission_mode](#session-keys-0-5----keys).

Edits are written back with `ruamel.yaml`'s round-trip mode, not a full re-dump -- only the values you actually changed are touched, so hand-written comments, key order, and formatting elsewhere in the file survive. This matters because these files are meant to be read (and still hand-editable) even if you mostly use the UI.

Out of scope for the UI: `config/usage.yaml`.

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

The OLED shows five lines total: two usage metrics (`5H [bar] 42%` / `7D [bar] 18%`, no header), a third line for the selected session's model name and (if known) effort level shown together as `Sonnet 4.5 - high`, a fourth line for the selected session's project label, and a fifth/bottom line for that session's current git branch, e.g. `main` (from `sessions.detect_branch()` -- runs `git branch --show-current` against the key's `project_path`, not a tmux-pane scrape) with a `*MIC* ` marker prefixed onto it while voice recording is active or a transcript is pending review, e.g. `*MIC* main`. The marker disappears once the mic goes idle, so the line spends most of its time just showing the branch -- deliberately terse to leave room for the branch name within the OLED's 128px width at the built-in font's ~6px glyph width (about 21 characters). To change how the usage bars *look* (e.g. wider bars) without touching the token-counting logic, edit the constants and `_render_bar`/`_render_metric_line`/`_apply_usage_message` near the top of `code.py`. `BAR_WIDTH` is deliberately conservative (10 chars) to leave room for the label and percentage on the same line. The third/fourth/fifth lines' rendering lives in `_apply_model_message`/`_apply_effort_message`/`_apply_label_message`/`_apply_branch_message`/`_render_branch_line`.

The encoder's rotation mode (`session` vs `effort` -- see `config/bridge.yaml`'s `encoder.rotation_mode` above) is pushed from the host as `{"encoder_mode": "..."}` and stored in `_encoder_mode`; `_poll_encoder()` branches on it to decide whether a detent updates `_selected_key` (sending `{"selected": N}`) or forwards the raw delta to the host as `{"encoder_delta": N}` for the bridge to turn into an `/effort` cycle. The board has no config file of its own, so this mode is always host-driven, not something you set locally on the pad.

**Gotcha if you add more OLED lines/colors**: the built-in OLED is monochrome, 1-bit-per-pixel -- `displayio` auto-converts any `label.Label(..., color=...)` you pick to plain black or white by luminance, there's no dimming/tinting. A color that "should" read as dim grey or dark red (anything with roughly less than half the luminance of white) silently converts to *black*, i.e. invisible text, not a dimmer version of it -- this is exactly what happened when the bottom status line first shipped with `0x606060`/`0xFF0000`, and it never rendered at all until switched to plain `0xFFFFFF`. Stick to `0xFFFFFF` (or another color with luminance clearly above ~50%, like the label line's `0x00CFFF`) for anything you actually want visible, and use the *text* itself, not color, to carry any on/off-style distinction.

Per-key LED rendering (both the selection brightness boost and the status pulse) happens every main-loop iteration in `_render_key_leds()`, driven by the `_key_colors`/`_key_pulsing` caches that `_apply_led_message()` fills in from each `{"key", "color", "pulse"}` message. To change the pulse animation's speed/depth or the selection brightness boost, edit `_PULSE_PERIOD_SECONDS`/`_PULSE_MIN_SCALE`/`_SELECTED_BRIGHTNESS_SCALE` near `_render_key_leds()`. `SESSION_KEY_COUNT` (how many keys the encoder cycles through) and `ACTION_KEY_START` (where action keys begin) are also constants there if you ever want a different split than 6/6 -- just remember to keep `config/keymap.yaml`'s `actions:` indices and `macropad/sessions.py`'s `SESSION_KEY_COUNT` in sync if you do.

**Voice press/release timing** (push-to-talk vs. tap-to-toggle -- see [Voice input](#voice-input-speak-instead-of-typing) above) is tracked entirely in firmware, since it needs sub-poll-interval press/release timestamps the host can't observe directly:

- The encoder's push-button is polled by `_poll_encoder_switch()` using the MacroPad library's built-in debouncer (`macropad.encoder_switch_debounced`, `.pressed`/`.released`). On press it records `time.monotonic()` and sends `{"voice_press": true, "source": "encoder"}`; on release it computes elapsed milliseconds and sends `{"voice_release": true, "source": "encoder", "held_ms": N}`.
- Whichever action key (if any) has `type: voice_toggle` is announced by the host as `{"voice_key": N}` (or `null`) -- see `macropad/bridge.py`'s `_push_voice_key()`. `_poll_keys()` checks incoming key events against `_voice_key` *before* the normal action-key dispatch, and if it matches, tracks that key's own press/release timing the same way instead of sending the usual one-shot `{"action": N}`.
- The bridge (`macropad/bridge.py`'s `_on_voice_press`/`_on_voice_release`) is what actually interprets `held_ms` against `voice.hold_threshold_ms` and decides push-to-talk vs. tap-to-toggle vs. submit-pending-review -- the board itself has no opinion on timing thresholds, it just reports what happened and when.
- The mic key's LED (`_mic_led_state`, set via `{"mic_led": "idle"|"recording"|"pending_review"}`) is rendered as an override in `_render_key_leds()` -- see `_MIC_IDLE_COLOR`/`_MIC_RECORDING_COLOR`/`_MIC_PENDING_REVIEW_COLOR` near the mic key/LED section of `code.py` if you want to change the colors. `recording` pulses using the same `pulse_scale` as status-pulsing keys; `idle`/`pending_review` are flat.
