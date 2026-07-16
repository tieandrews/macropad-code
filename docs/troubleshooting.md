# Troubleshooting

## `macropad-bridge` never prints "connected to ..."

- Check the MacroPad shows up as a USB serial device at all:
  - macOS: `ls /dev/cu.usbmodem*`
  - Linux: `ls /dev/ttyACM*`
  - Windows: check Device Manager under "Ports (COM & LPT)"
- Confirm `firmware/boot.py` is actually on the `CIRCUITPY` drive and you **unplugged and replugged** after copying it -- `usb_cdc.enable(..., data=True)` only takes effect on the next boot.
- If you have `adafruit-board-toolkit` installed, port detection is used automatically; without it, the bridge falls back to scanning for Adafruit's USB vendor ID (`0x239A`) or a product string containing "macropad". If your OS reports something unusual, run this to see what's plugged in:

  ```bash
  python3 -c "from serial.tools import list_ports; [print(p.device, p.vid, p.product) for p in list_ports.comports()]"
  ```

## Hooks fire but no LED changes

1. Is `macropad-bridge` actually running? It logs every state change it receives -- if you don't see a log line at all when you'd expect one, the hook isn't reaching it (see below), not a serial problem.
2. Is the key assigned? Hooks silently no-op if the session's working directory doesn't match any `project_path` in `config/keymap.yaml`. Check with:

   ```bash
   python3 -c "from macropad import config; print(config.key_for_path('/path/you/were/in'))"
   ```

   `None` means no match -- see [customization.md](customization.md).
3. Test the hook script directly:

   ```bash
   echo '{"hook_event_name":"Stop","cwd":"/path/from/your/keymap"}' | python3 macropad/hooks/claude_hook.py
   ```

   Then check the bridge's log for a `key=... state=done` line.

## Claude Code doesn't seem to run the hook at all

- Confirm the hook actually landed in `~/.claude/settings.json` under `"hooks"` and that the `command` path points at a real, executable Python (`sys.executable` at setup time -- if you've since moved/reinstalled Python, re-run `macropad-setup`).
- Claude Code hooks are per-event; if you only see `UserPromptSubmit` firing but never `Notification`/`Stop`, that's expected during a long single turn -- `Stop` only fires once Claude actually finishes.

## Codex CLI doesn't light anything up

This is partly expected -- see the "Known limitation" note in [agent-integration.md](agent-integration.md). Codex's `notify` only reports `agent-turn-complete`, so a Codex-assigned key only ever shows `done`, never `working`/`waiting`/`permission`. If nothing lights up even for `done`:

- Confirm `notify = [...]` actually landed as a **top-level** key in `~/.codex/config.toml`, i.e. before any `[table]` header. `macropad-setup` inserts it there specifically because a `notify` line placed after a `[table]` header would be silently swallowed into that table instead of being read as global config.
- Test the hook directly:

  ```bash
  python3 macropad/hooks/codex_hook.py '{"type":"agent-turn-complete","cwd":"/path/from/your/keymap"}'
  ```

## "Address already in use" when starting the bridge

Something else is using port `9999` (or you already have a bridge running). Either stop the other process, or change `port` in `config/bridge.yaml` and restart.

## The pad is too bright / too dim

Adjust `brightness` in `config/colors.yaml` (0.0-1.0) -- it scales every color uniformly. There's also a hardcoded `macropad.pixels.brightness = 0.3` in `firmware/code.py` as a hardware-side ceiling; lower that too if you want a dimmer maximum regardless of what the host sends.

## I broke `~/.claude/settings.json` or `~/.codex/config.toml`

`macropad-setup` backs up both files before touching them (`<file>.bak-<timestamp>`, next to the original). Restore from the most recent backup and re-run setup.
