# Troubleshooting

## `macropad-bridge` never prints "connected to ..."

- Check the MacroPad shows up as a USB serial device at all:
  - macOS: `ls /dev/cu.usbmodem*`
  - Linux: `ls /dev/ttyACM*`
  - Windows: check Device Manager under "Ports (COM & LPT)"
- Confirm `firmware/boot.py` is actually on the `CIRCUITPY` drive and you **unplugged and replugged** after copying it -- `usb_cdc.enable(..., data=True)` only takes effect on the next boot.
- If you have `adafruit-board-toolkit` installed, port detection is used automatically; without it, the bridge falls back to scanning for Adafruit's USB vendor ID (`0x239A`) or a product string containing "macropad". If your OS reports something unusual, run this to see what's plugged in:

  ```bash
  uv run python3 -c "from serial.tools import list_ports; [print(p.device, p.vid, p.product) for p in list_ports.comports()]"
  ```

## Hooks fire but no LED changes

1. Is `macropad-bridge` actually running? It logs every state change it receives -- if you don't see a log line at all when you'd expect one, the hook isn't reaching it (see below), not a serial problem.
2. Is the key assigned? Hooks silently no-op if the session's working directory doesn't match any `project_path` in `config/keymap.yaml`. Check with:

   ```bash
   uv run python3 -c "from macropad import config; print(config.key_for_path('/path/you/were/in'))"
   ```

   `None` means no match -- see [customization.md](customization.md).
3. Test the hook script directly:

   ```bash
   echo '{"hook_event_name":"Stop","cwd":"/path/from/your/keymap"}' | uv run python3 macropad/hooks/claude_hook.py
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
  uv run python3 macropad/hooks/codex_hook.py '{"type":"agent-turn-complete","cwd":"/path/from/your/keymap"}'
  ```

## The OLED usage dashboard is blank or stuck at "0"

1. Check `enabled: true` in `config/usage.yaml` -- and that the bridge log printed `usage display enabled (source=..., every Ns)` on startup. If that line is missing, the bridge read `enabled: false` (or the file failed to parse).
2. Confirm Claude Code has actually written transcripts to scan (this applies to `source: local_estimate`, and to any other source while it's falling back):

   ```bash
   uv run python3 -c "from macropad import usage; print(usage.compute_usage())"
   ```

   If this prints `{'session_tokens': 0, 'weekly_tokens': 0}` but you know you've used Claude Code recently, check that `~/.claude/projects/` exists and has recently-modified `.jsonl` files under it -- a nonstandard `CLAUDE_CONFIG_DIR` or a Claude Code install that stores state elsewhere would make this always read 0. See [customization.md](customization.md) for the exact fields being parsed.
3. Remember `local_estimate` is deliberately a *rough* number, not a live "% of quota" unless you've set `session_token_budget`/`weekly_token_budget` yourself -- see [customization.md](customization.md) if the numbers look plausible but the bars never move the way you expect.

## `source: claude_monitor` never shows real percentages

```bash
uv run python3 -c "from macropad import usage_monitor; print(usage_monitor.read_monitor_percentages())"
```

- `None` with no error means either the state file doesn't exist (the monitor tool isn't running, or writes somewhere other than `~/.claude-monitor/state/latest.json` -- set `claude_monitor.state_path` in `config/usage.yaml` if so) or it exists but none of the key names `macropad/usage_monitor.py` looks for were found. Open the file yourself and compare its actual keys against `_SESSION_KEYS`/`_WEEKLY_KEYS` in that module -- we haven't verified this against a live install, so the real schema may differ.
- Getting `None` here means the bridge is (correctly, silently) falling back to `local_estimate` -- that's not a bug, it's the intended degrade path.

## `source: claude_pty` never shows real percentages

This is the most likely of the three sources to need troubleshooting -- see the "experimental" section in [customization.md](customization.md) for the full tradeoffs. Test it directly and watch what actually happens:

```bash
uv run python3 -c "
from macropad import usage_pty
print(usage_pty.get_usage_percentages(working_dir=None, timeout_seconds=20))
"
```

- `None` almost always means `claude` never reached a ready chat prompt within the timeout -- most commonly because the onboarding wizard (theme/login) or a directory-trust prompt was still showing. **Run `claude` by hand** from the same `working_dir` you configured and confirm it goes straight to a normal chat, with no prompts, before this will ever succeed. We verified directly while building this that a fresh, never-used-here `claude` process lands on the onboarding wizard rather than a ready session -- this source deliberately detects that and gives up cleanly rather than hang or misread wizard text as usage numbers.
- If `claude` *is* fully set up and this still returns `None`, the `/usage` panel's real wording may not match the regexes in `macropad/usage_pty.py` (`_SESSION_PCT_RE`/`_WEEKLY_PCT_RE`) -- we were not able to verify the exact current text of a real `/usage` panel while building this (only the failure screens). Run `claude` by hand, type `/usage`, and compare what you see against those patterns; adjust them if the wording has changed.
- Confirm no orphaned `claude` processes are piling up (`ps aux | grep claude`) -- there shouldn't be any, since the process is always killed in a `finally` block, but if you see one, that's worth reporting.
- If you're not sure whether `pyte` is installed, `uv run python3 -c "import pyte"` -- an `ImportError` means the bridge logged a warning and is using the cruder fallback ANSI stripper. `uv sync --extra pty` to fix.

## "Address already in use" when starting the bridge

Something else is using port `9999` (or you already have a bridge running). Either stop the other process, or change `port` in `config/bridge.yaml` and restart.

## The pad is too bright / too dim

Adjust `brightness` in `config/colors.yaml` (0.0-1.0) -- it scales every color uniformly. There's also a hardcoded `macropad.pixels.brightness = 0.3` in `firmware/code.py` as a hardware-side ceiling; lower that too if you want a dimmer maximum regardless of what the host sends.

## I broke `~/.claude/settings.json` or `~/.codex/config.toml`

`macropad-setup` backs up both files before touching them (`<file>.bak-<timestamp>`, next to the original). Restore from the most recent backup and re-run setup.
