# Customizing keymap, colors, and the bridge

All three config files live in [`config/`](../config/) and are plain YAML -- edit them in any text editor. `macropad-bridge` reads `config/colors.yaml` and `config/bridge.yaml` at startup (restart it after editing those), and `config/keymap.yaml` on every hook event (no restart needed).

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

## Firmware-side changes

Per-key LED behavior (brightness clamp, how JSON messages are parsed) lives in [`firmware/code.py`](../firmware/code.py) on the MacroPad itself, not in this repo's Python. If you change it, re-copy the file to `CIRCUITPY/code.py` -- no reboot needed, CircuitPython reloads automatically on save.
