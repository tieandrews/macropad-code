# macropad-code

Turn an [Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a live per-key status light *and* a control surface for Claude Code and/or Codex CLI sessions -- a DIY, ~CA$75 alternative to OpenAI's $230 Codex Micro. The built-in OLED doubles as a live Claude Code usage dashboard.

The 12 keys split into two roles:

- **Session keys (top two rows, 0-5)**: each one you assign to a project lights up with the state of the agent running there. Rotate the encoder to cycle which one is "selected" (its LED gets brighter), or just press the key you want directly -- e.g. spot one pulsing red and press it, no need to scroll over. Pulsing is reserved separately for statuses worth interrupting you for (`permission`, `error` by default), independent of selection.
- **Action keys (bottom two rows, 6-11)**: send a configured bit of input -- approve, deny, `/usage`, switch models, whatever you like -- straight into whichever session is currently selected, via a `tmux` pane running that session. See [Action keys: steering a session](#action-keys-steering-a-session) below.
- **The encoder itself**: press it to toggle voice-to-text -- speak a command and it's typed into the selected session for you, transcribed fully offline. See [Voice input](#voice-input) below.

| State | Default color | Meaning |
|---|---|---|
| State | Default color | Pulses? | Meaning |
|---|---|---|---|
| `working` | amber | no | agent is actively thinking / running |
| `waiting` | magenta | no | agent finished its turn, waiting on you |
| `permission` | red | yes | agent needs your approval to continue |
| `done` | green | no | agent completed successfully |
| `error` | dark red | yes | agent or a hook hit an error |
| `idle` | dim green | no | no active session |

The OLED shows a compact dashboard: two lines of current session/trailing-week token usage (Claude Code only), read from Claude Code's own local session history, a third line showing which model the selected session is using, a fourth line showing which repo/project it is, and a bottom line for mic/voice-input status -- see [Usage display](#usage-display) below.

## How it works

```
Claude Code / Codex CLI hook fires
  -> a tiny hook script maps the event to a state
  -> macropad-bridge (background daemon) resolves the state to a color
     and pushes it to the MacroPad over USB serial
  -> the MacroPad's firmware lights the matching key
```

The action keys work in reverse -- MacroPad -> agent, not agent -> MacroPad:

```
rotate the encoder -> firmware brightens the selected session key's LED
                    -> tells the bridge which key (0-5) is selected
                    -> bridge pushes that key's model + repo label to the OLED
press an action key -> bridge looks up what to send in config/keymap.yaml
                     -> sends it into that session's tmux pane
```

See [docs/agent-integration.md](docs/agent-integration.md) for both wiring diagrams in full.

## Quickstart

This project uses [uv](https://docs.astral.sh/uv/) to manage the Python environment -- no manual `venv`/`pip` juggling, and `uv run`/`uv sync` stay in lockstep with `uv.lock`.

1. **Flash the MacroPad.** Follow [docs/hardware-setup.md](docs/hardware-setup.md) to install CircuitPython and copy `firmware/boot.py` + `firmware/code.py` onto it.

2. **Install this repo's Python side:**

   ```bash
   git clone <this repo>
   cd macropad-code
   curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if you already have uv
   uv sync
   ```

   This creates `.venv/` and installs exactly what's pinned in `uv.lock`.

3. **Run the interactive setup:**

   ```bash
   uv run macropad-setup
   ```

   It will ask which agent(s) you use (Claude Code, Codex CLI, or both), wire the matching hooks into `~/.claude/settings.json` and/or `~/.codex/config.toml`, let you assign MacroPad keys to project directories, offer to enable the OLED usage dashboard, and optionally install `macropad-bridge` as a background service so it starts automatically at login.

4. **Start using it.** Run Claude Code or Codex CLI from a project directory you assigned to a key, and that key's LED should track the session's state. If you didn't install the background service, start it manually first: `uv run macropad-bridge`.

No `uv`? `pip install -e .` (or `pip install -r requirements.txt`) still works -- see [docs/running-the-bridge.md](docs/running-the-bridge.md) for both paths.

## Usage display

The MacroPad's screen can show a live dashboard of your Claude Code token usage, plus which model and project the selected session is using:

```
5H [####......] 42%
7D [##........] 18%
Sonnet 4.5
emexams-website
MIC: off
```

There's no official Anthropic API for your exact Pro/Max session/weekly quota, so `config/usage.yaml`'s `source:` lets you pick how these numbers get populated:

- **`local_estimate`** (default) -- sums tokens from Claude Code's own local session transcripts. Always works, zero setup, but shows a raw count rather than Anthropic's real percentage unless you set your own estimated budget.
- **`claude_monitor`** -- real Anthropic percentages, if you already run the community [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) tool.
- **`claude_pty`** (experimental) -- real Anthropic percentages by periodically driving `claude` itself through its `/usage` command. Heavier and more fragile -- see [docs/customization.md](docs/customization.md) for the tradeoffs we found while building it (it needs `claude` already fully logged in with the target directory already trusted, or it detects that and cleanly falls back rather than hang).

Whichever you pick, a failure always falls back to `local_estimate` rather than showing nothing. Codex CLI has no local equivalent to any of these, so usage display is Claude Code only.

The model and project lines are independent of `usage.yaml` entirely -- the model is detected by the bridge snapshotting the *selected* session's tmux pane every few seconds (`config/bridge.yaml`'s `model_poll_interval_seconds`), so it's only available once you've got that session running via `macropad-sessions` (below); the project line is just that key's `label` from `config/keymap.yaml`, so it's available as soon as the key is configured.

## Action keys: steering a session

Pick which session key (0-5) is "selected" either by rotating the encoder to it or just pressing it directly -- its LED gets brighter so you can tell at a glance (pulsing is reserved for statuses worth interrupting you for, see the state table above). Press an action key (6-11) and whatever you configured for it gets typed straight into that session, wherever it's actually running:

```yaml
# config/keymap.yaml
actions:
  6:
    label: Approve
    send_keys: "y"
  7:
    label: Deny
    send_keys: "n"
  8:
    label: Switch model
    type: cycle_model
    models: [sonnet, opus, haiku]
```

This works by running each session inside a named `tmux` pane rather than trying to guess/focus the right terminal window (which has no reliable cross-platform way to do). `uv run macropad-sessions` starts a tmux pane per configured key and launches the agent inside it -- `claude --remote-control "<label>"` for Claude Code, so you can also review or steer it from `claude.ai/code` or the Claude mobile app, not just the physical pad. Requires `tmux` installed and, for Remote Control, a Pro/Max/Team/Enterprise plan. It's safe (and normal) to re-run `macropad-sessions` any time -- see [docs/running-sessions.md](docs/running-sessions.md) for starting/cleaning up sessions, and [docs/customization.md](docs/customization.md#wiring-action-keys-to-a-live-session-tmux--remote-control) for the full setup.

To actually *look at* whichever session is currently selected without repeatedly typing `tmux attach -t macropad-key<N>`, run `uv run macropad-sessions --follow` once and leave it attached in a spare terminal/pane -- it live-follows the encoder, updating in place every time you rotate the knob (`macropad-bridge` keeps it re-pointed via `tmux link-window`). See [docs/running-sessions.md](docs/running-sessions.md#auto-following-the-encoder-selection-macropad-follow).

`type: cycle_model` action keys send Claude Code's own `/model <name>` command, advancing through `models:` one press at a time -- the OLED's model line above picks up the change within a few seconds.

## Voice input

Press the rotary encoder to start recording from your microphone, press it again to stop -- the transcription (fully offline, via [faster-whisper](https://github.com/SYSTRAN/faster-whisper), no API key or cloud round-trip) gets typed straight into the selected session, same as an action key. The OLED's bottom line shows `MIC: REC` the whole time, `MIC: off` otherwise.

Off by default -- it needs the optional `voice` extra (`uv sync --extra voice`), the system-level PortAudio library (`sudo apt install libportaudio2` on Debian/Ubuntu/WSL), and a microphone actually reachable from wherever the bridge runs. Then set `voice.enabled: true` in `config/bridge.yaml` and restart the bridge. See [docs/customization.md](docs/customization.md#voice-input-speak-instead-of-typing) for the full setup, including WSL-specific microphone notes.

## Customizing

- **Which key maps to which project, and what action keys send:** [`config/keymap.yaml`](config/keymap.yaml)
- **What each state looks like:** [`config/colors.yaml`](config/colors.yaml)
- **Bridge daemon settings (port, reconnect interval, voice input, model-poll interval):** [`config/bridge.yaml`](config/bridge.yaml)
- **OLED usage dashboard (windows, budgets, poll interval):** [`config/usage.yaml`](config/usage.yaml)

All four are plain YAML -- see [docs/customization.md](docs/customization.md) for the full reference. No code changes needed for everyday tweaks.

## Docs

- [docs/hardware-setup.md](docs/hardware-setup.md) -- flashing the MacroPad
- [docs/agent-integration.md](docs/agent-integration.md) -- how hook events become LED colors
- [docs/customization.md](docs/customization.md) -- keymap / colors / bridge settings
- [docs/running-the-bridge.md](docs/running-the-bridge.md) -- running `macropad-bridge` manually or as a service
- [docs/running-sessions.md](docs/running-sessions.md) -- starting/cleaning up the `tmux` sessions action keys route into
- [docs/troubleshooting.md](docs/troubleshooting.md) -- common problems, including updating firmware reliably over WSL

## Repo layout

```
firmware/           CircuitPython firmware that runs on the MacroPad itself
macropad/            host-side Python package
  config.py           loads config/*.yaml
  serial_link.py       finds + maintains the USB serial connection (both directions)
  usage.py              local-estimate usage + the source dispatcher
  usage_monitor.py       optional Claude-Code-Usage-Monitor integration
  usage_pty.py            experimental live `claude /usage` scraper
  voice.py               optional mic recording + local Whisper transcription
  bridge.py            the background daemon (macropad-bridge)
  sessions.py           tmux + `claude --remote-control` launcher (macropad-sessions), model detection
  firmware_flash.py       pushes firmware/*.py over the serial REPL (macropad-flash) -- see docs/troubleshooting.md
  client.py            tiny client hooks use to talk to the daemon
  setup_cli.py          the interactive `macropad-setup` command
  hooks/               Claude Code / Codex CLI hook entry points
  services/             launchd / systemd / Windows startup templates
config/              human-edited YAML: keymap.yaml (keys + actions), colors.yaml, bridge.yaml, usage.yaml
docs/                see above
bin/                 convenience entry points that work without `uv`/`pip install -e .`
pyproject.toml       package + dependency spec (uv/pip compatible)
uv.lock              pinned dependency versions -- commit this, `uv sync` reads it
```

## Status

This is a hobby project, not affiliated with OpenAI, Anthropic, or Adafruit.
