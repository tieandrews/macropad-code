# Customizing keymap, colors, usage display, and the bridge

All config files live in [`config/`](../config/) and are plain YAML -- edit them in any text editor. `macropad-bridge` reads `config/colors.yaml`, `config/bridge.yaml`, and `config/usage.yaml` at startup (restart it after editing those), and `config/keymap.yaml` on every hook event (no restart needed).

## `config/keymap.yaml` -- which key belongs to which project

The MacroPad's 12 keys are numbered 0-11, left-to-right then top-to-bottom:

```
 0   1   2
 3   4   5
 6   7   8
 9  10  11
```

Each entry under `keys:` maps a key index to a project:

```yaml
keys:
  0:
    label: "macropad-code"
    agent: claude               # claude | codex -- informational, not enforced
    project_path: "~/projects/macropad-code"
  1:
    label: "api-server"
    agent: codex
    project_path: "~/projects/api-server"
```

When a hook fires, it reads the working directory (`cwd`) of the agent session and looks for the entry whose `project_path` is the closest ancestor of that directory -- so a session running in `~/projects/macropad-code/frontend` still matches the `~/projects/macropad-code` entry. If no entry matches, nothing lights up (the hook is a no-op).

You can hand-edit this file directly, or run `macropad-setup` again to add more keys interactively (it won't remove or touch keys you've already configured unless you overwrite them with the same index).

`project_path` accepts `~` and both absolute and relative-to-home paths. It does **not** need to exist yet at assignment time -- `macropad-setup` will warn but still save it.

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
  # ...
```

- `color` is `[R, G, B]`, each `0-255`.
- `brightness` is a single global multiplier -- turn it down if the pad is uncomfortably bright next to a monitor, rather than editing every color individually.
- To add a brand-new state, add it here *and* map an event to it in `macropad/hooks/*.py` (see [agent-integration.md](agent-integration.md)) -- a state with no color entry falls back to `idle`'s color.

Changes take effect the next time the bridge resolves a state to a color, i.e. on the next hook event after you restart `macropad-bridge`.

## `config/bridge.yaml` -- daemon settings

```yaml
host: "127.0.0.1"
port: 9999

serial:
  baudrate: 115200
  retry_seconds: 2.0
```

- `host`/`port`: the local socket hook scripts use to talk to the bridge. Only change this if `9999` is already taken by something else on your machine -- and if you do, restart the bridge.
- `serial.retry_seconds`: how often the bridge tries to (re)find the MacroPad if it's unplugged, asleep, or not yet connected. Lower it if you want faster reconnects at the cost of slightly more CPU/USB polling.

## `config/usage.yaml` -- OLED usage dashboard

```yaml
enabled: true
poll_interval_seconds: 60
session_window_hours: 5
weekly_window_days: 7
session_token_budget: null
weekly_token_budget: null
```

The bridge periodically scans Claude Code's own local session transcripts under `~/.claude/projects/**/*.jsonl` and sums the token usage recorded in them over two rolling windows: the last `session_window_hours` and the last `weekly_window_days`. The result gets pushed straight to the MacroPad's OLED (independent of any key -- it's a single shared dashboard, not per-project).

**Why raw token counts, not a percentage?** Anthropic doesn't publish the exact token budget behind the "session" and "weekly" limits shown in Claude Code's own `/usage` command, and there's no documented API a local script can query for it -- so we don't fabricate a number we can't back up. By default `session_token_budget`/`weekly_token_budget` are `null` and the OLED just shows a raw count (`128.4K`, `1.9M`, ...). If you've empirically learned roughly where your own plan's limits kick in, set either budget to your own estimated token ceiling and that metric switches to a percentage bar instead:

```yaml
session_token_budget: 500000
weekly_token_budget: 2000000
```

Other notes:

- **Claude Code only.** Codex CLI doesn't write anything locally that this can read.
- `poll_interval_seconds` trades freshness for disk I/O -- the scan reads every transcript file modified within the weekly window on every tick. 60s is a reasonable default; raise it if you have a very large `~/.claude/projects/` history.
- The transcript JSONL format is internal to Claude Code and can change between releases. `macropad/usage.py` parses it defensively (any line, file, or field it doesn't recognize is skipped, not raised) so a format change degrades to "shows 0" rather than crashing the bridge -- but the numbers could in principle go stale if Anthropic changes the schema. If that happens, check the field names `macropad/usage.py` looks for (`message.usage.{input,output,cache_creation_input,cache_read_input}_tokens` and a top-level `timestamp`) against a real file in `~/.claude/projects/` to see what changed.
- Toggle it on/off any time with `uv run macropad-setup` (it only touches the `enabled:` line, your comments and other settings stay put), or hand-edit `enabled: true`/`false` directly.

## Firmware-side changes

Per-key LED behavior and the OLED dashboard layout (bar width, line spacing, brightness clamp, how JSON messages are parsed) live in [`firmware/code.py`](../firmware/code.py) on the MacroPad itself, not in this repo's Python. If you change it, re-copy the file to `CIRCUITPY/code.py` -- no reboot needed, CircuitPython reloads automatically on save.

To change how the usage bars *look* (e.g. wider bars, different labels) without touching the token-counting logic, edit the constants and `_render_bar`/`_apply_usage_message` near the top of `code.py`. `BAR_WIDTH` is deliberately conservative (12 chars) to stay within the OLED's 128px width at the built-in font's ~6px glyph width -- widen it carefully if you switch fonts.
