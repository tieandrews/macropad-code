# macropad-code

Turn an [Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) into a live per-key status light for Claude Code and/or Codex CLI sessions -- a DIY, ~CA$75 alternative to OpenAI's Codex Micro's "Agent Keys." The built-in OLED doubles as a live Claude Code usage dashboard.

Each key you assign to a project lights up with the state of the agent running there:

| State | Default color | Meaning |
|---|---|---|
| `working` | amber | agent is actively thinking / running |
| `waiting` | magenta | agent finished its turn, waiting on you |
| `permission` | red | agent needs your approval to continue |
| `done` | green | agent completed successfully |
| `error` | dark red | agent or a hook hit an error |
| `idle` | dim green | no active session |

The OLED shows a small dashboard of your current session and trailing-week token usage (Claude Code only), read from Claude Code's own local session history -- see [Usage display](#usage-display) below.

## How it works

```
Claude Code / Codex CLI hook fires
  -> a tiny hook script maps the event to a state
  -> macropad-bridge (background daemon) resolves the state to a color
     and pushes it to the MacroPad over USB serial
  -> the MacroPad's firmware lights the matching key
```

See [docs/agent-integration.md](docs/agent-integration.md) for the full wiring diagram.

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

The MacroPad's screen can show a live dashboard of your Claude Code token usage:

```
AGENT USAGE
SESSION 5H: 128.4K
[####........] 42%
WEEK 7D: 1.9M
[##..........] 18%
```

This reads Claude Code's own local session transcripts (`~/.claude/projects/`) -- the same place its `/usage` command draws from. There's no official, published API for your exact plan quota, so by default the pad shows **raw token counts** rather than a fabricated percentage. If you want a percentage bar instead, set your own estimated `session_token_budget`/`weekly_token_budget` in [`config/usage.yaml`](config/usage.yaml) -- see [docs/customization.md](docs/customization.md) for details. Codex CLI has no local equivalent to read, so this is Claude Code only.

## Customizing

- **Which key maps to which project:** [`config/keymap.yaml`](config/keymap.yaml)
- **What each state looks like:** [`config/colors.yaml`](config/colors.yaml)
- **Bridge daemon settings (port, reconnect interval):** [`config/bridge.yaml`](config/bridge.yaml)
- **OLED usage dashboard (windows, budgets, poll interval):** [`config/usage.yaml`](config/usage.yaml)

All four are plain YAML -- see [docs/customization.md](docs/customization.md) for the full reference. No code changes needed for everyday tweaks.

## Docs

- [docs/hardware-setup.md](docs/hardware-setup.md) -- flashing the MacroPad
- [docs/agent-integration.md](docs/agent-integration.md) -- how hook events become LED colors
- [docs/customization.md](docs/customization.md) -- keymap / colors / bridge settings
- [docs/running-the-bridge.md](docs/running-the-bridge.md) -- running `macropad-bridge` manually or as a service
- [docs/troubleshooting.md](docs/troubleshooting.md) -- common problems

## Repo layout

```
firmware/           CircuitPython firmware that runs on the MacroPad itself
macropad/            host-side Python package
  config.py           loads config/*.yaml
  serial_link.py       finds + maintains the USB serial connection
  usage.py              reads local Claude Code usage from ~/.claude/projects/
  bridge.py            the background daemon (macropad-bridge)
  client.py            tiny client hooks use to talk to the daemon
  setup_cli.py          the interactive `macropad-setup` command
  hooks/               Claude Code / Codex CLI hook entry points
  services/             launchd / systemd / Windows startup templates
config/              human-edited YAML: keymap.yaml, colors.yaml, bridge.yaml, usage.yaml
docs/                see above
bin/                 convenience entry points that work without `uv`/`pip install -e .`
pyproject.toml       package + dependency spec (uv/pip compatible)
uv.lock              pinned dependency versions -- commit this, `uv sync` reads it
```

## Status

This is a hobby project, not affiliated with OpenAI, Anthropic, or Adafruit.
