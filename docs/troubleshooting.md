# Troubleshooting

## The OLED shows a CircuitPython version banner / "Press any key to enter the REPL" instead of going blank

This is **not** bootloader mode -- check the drive name. `RPI-RP2` means bootloader; `CIRCUITPY` means CircuitPython is running normally. What you're seeing is CircuitPython's default serial console, which the MacroPad's built-in OLED mirrors until `code.py` takes over the display -- so this means `code.py` crashed (or never started) before it got that far, not that flashing failed. (`code.py`'s usage dashboard has no header text of its own -- a working, freshly-booted pad with usage display not yet enabled/polled just shows a blank screen, not a banner.)

The most common cause is a missing library under `CIRCUITPY/lib/`. `code.py` calls `MacroPad()` on its very first real line, and that constructor needs more than the four libraries most guides mention -- see the full list in [hardware-setup.md](hardware-setup.md#2-install-the-circuitpython-libraries). Any one missing raises an `ImportError` immediately.

To see the actual error:

1. Open a serial terminal (e.g. `screen /dev/ttyACM0 115200` on Linux, `tio`, Mu's serial console, or the Arduino/Thonny serial monitor) to the MacroPad's **console** port -- not the `data` port `macropad-bridge` uses.
2. Press <kbd>Ctrl</kbd>+<kbd>D</kbd> to force a reload and watch the traceback that prints before the "Code done running" banner.
3. Fix whatever it names (usually `ModuleNotFoundError`/`ImportError: no module named '...'` -- copy that specific file/folder from the library bundle into `CIRCUITPY/lib/`) and it should reload automatically once the missing file is in place.

If you're on WSL, note that `CIRCUITPY` mounts as a normal USB drive to **Windows**, not WSL, unless you've set up USB passthrough (e.g. `usbipd-win`) -- drag files onto it from Windows Explorer, and likewise run any serial terminal from Windows (or through `usbipd`) rather than expecting `/dev/ttyACM*` to exist inside WSL by default. Once passthrough is set up, use `uv run macropad-flash` for `code.py`/`boot.py` updates specifically (not a drag-and-drop copy) -- see "Updating firmware over WSL/USB" below for why.

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

## The OLED usage lines fell back to raw token counts (e.g. "203.4K") instead of percentages

Check the bridge's log for the most recent `usage ... -> sent` line -- as of this writing, a fallback poll is tagged with the reason right there (`fell back from claude_pty -- it returned no result this poll, ...`), so you don't have to guess:

```bash
journalctl --user -u macropad-bridge.service --no-pager | grep 'usage ' | tail -5
```

This is usually transient and self-heals on the next poll (`poll_interval_seconds` in `config/usage.yaml`, default 600s for `claude_pty`) -- `get_usage_percentages()` never raises, it just returns `None` (and the bridge silently falls back) whenever that one poll's `claude` launch didn't fully boot to a ready prompt and print `/usage` within `timeout_seconds`. Heavy concurrent load on the machine at that exact moment (e.g. you're mid-way through `macropad-flash`, a big build, ...) is enough to cause one poll to time out this way without anything actually being misconfigured. If it's *consistently* falling back on every poll rather than just occasionally, see the next section.

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

## The OLED's third line (model name) never shows anything

1. Confirm a tmux session is actually running for whichever key is currently selected: `tmux has-session -t macropad-key0` (swap in the right key index) -- if that fails, run `uv run macropad-sessions` first.
2. Test detection directly:

   ```bash
   uv run python3 -c "from macropad import sessions; print(sessions.detect_model(0))"
   ```

   `None` means either the session isn't running, or Claude Code's status line wasn't recognizable in the pane's last few lines (mid-redraw, a Codex CLI session which doesn't show a model name the same way, or wording that's changed since -- see `macropad/sessions.py`'s `_MODEL_RE`). Try `tmux capture-pane -t macropad-key0 -p` yourself and see what's actually there.
3. Rotating the encoder to reselect the same key forces an immediate re-check rather than waiting for the next `model_poll_interval_seconds` tick, if you want to rule out just needing to wait a few seconds.

## Voice input: encoder press does nothing / "voice input is disabled"

1. Check `voice.enabled: true` is actually set in `config/bridge.yaml`, and that you restarted the bridge after editing it (this file is only read at startup).
2. Check the optional dependencies are installed: `uv run python3 -c "import sounddevice, numpy, faster_whisper"` -- if any of those raise `ModuleNotFoundError`, run `uv sync --extra voice`.
3. Check the system-level PortAudio library is present (this is a separate, non-Python install): `uv run python3 -c "import sounddevice as sd; print(sd.query_devices())"`. `OSError: PortAudio library not found` means you still need `sudo apt install libportaudio2` (Debian/Ubuntu/WSL).
4. If PortAudio loads but `query_devices()` shows no usable input device (or every input shows 0 max input channels), the mic itself isn't reachable from wherever the bridge runs:
   - **On WSL**: check `pactl list sources short` first -- if that already shows an `RDPSource` line, WSLg is forwarding your Windows mic fine and the problem is purely that ALSA (which PortAudio uses) can't see it yet. The fix is `sudo apt install libasound2-plugins` (the ALSA-to-PulseAudio bridge plugin) -- `libportaudio2` alone is not enough. After installing it, `sd.query_devices()` should show a `pulse` device and `sd.default.device` should no longer be `[-1, -1]`.
   - If `pactl list sources short` shows nothing at all, WSLg itself isn't forwarding the mic -- try `wsl --shutdown` from PowerShell and reopening your WSL terminal, check Windows' microphone privacy settings (Settings -> Privacy & security -> Microphone) aren't blocking access, and confirm a mic works in some other Windows app first.
   - On any platform, check your OS's normal input-device settings -- if no app on the machine can record from it, nothing here will either.
5. `sounddevice.PortAudioError: Error starting stream: Wait timed out [PaErrorCode -9987]` (with `PaUnixThread_New`/`paTimedOut` in the traceback) is a [known PortAudio race-condition bug](https://github.com/PortAudio/portaudio/issues/1011) in its callback-based audio API, common under WSL/virtualized environments. `macropad/voice.py` already works around this by using PortAudio's blocking API instead -- if you see this error, you're likely running an older copy of `voice.py` (`git pull`/re-sync) or have modified it to use `InputStream(..., callback=...)`.
6. If recording starts fine (OLED's bottom line shows `MIC: REC`) but nothing gets typed after you stop, check the bridge's log -- `voice capture produced no usable transcription` usually means the recording was silence/too short, or the Whisper model failed to load (the first transcription after a bridge restart also downloads the model, which needs internet access once).

## "Address already in use" when starting the bridge

Something else is using port `9999` (or you already have a bridge running). Either stop the other process, or change `port` in `config/bridge.yaml` and restart.

## The pad is too bright / too dim

Adjust `brightness` in `config/colors.yaml` (0.0-1.0) -- it scales every color uniformly. There's also a hardcoded `macropad.pixels.brightness = 0.3` in `firmware/code.py` as a hardware-side ceiling; lower that too if you want a dimmer maximum regardless of what the host sends.

## Updating firmware over WSL/USB (write-protect errors, reverted files)

If you're editing `firmware/code.py` or `firmware/boot.py` and using WSL with the MacroPad passed through via `usbipd-win`, **don't copy files to the `CIRCUITPY` drive from WSL.** Mass-storage writes tunneled through `usbipd-win` into WSL are not reliable for this device -- `cp`, `sync`, and even `ls` right afterward can all report success while the bytes never actually reach flash, and repeated attempts can leave the FAT filesystem in a state that reports itself as write-protected (`Sense Key 0x7, ASC=0x27`) to *any* host afterward, tunneled or native. `dmesg` will show something like:

```
sd X:X:X:X: [sdX] tag#0 Sense Key : 0x7 [current]
sd X:X:X:X: [sdX] tag#0 ASC=0x27 ASCQ=0x0
critical target error, dev sdX, sector N op 0x1:(WRITE) ...
Buffer I/O error on dev sdX1, ... lost (a)sync page write
```

if you mount it from WSL and try to write.

### The fix: `macropad-flash` (serial REPL, not mass storage)

```bash
uv sync --extra firmware   # once
uv run macropad-flash
```

This pushes `firmware/boot.py` and `firmware/code.py` over the serial REPL (using [`ampy`](https://github.com/adafruit/adafruit_ampy)) instead of the mass-storage drive, then triggers a soft reboot. It's been completely reliable in comparison -- no tunnel, no FAT filesystem, just a byte stream, the same kind of connection `macropad-bridge` already uses successfully. See `macropad/firmware_flash.py`.

This *requires* `firmware/boot.py` to already have `storage.remount("/", readonly=False)` in it (it does, as shipped in this repo) -- that flips CircuitPython's default so the *runtime* side (and therefore the REPL/`ampy`) gets write access, at the cost of `CIRCUITPY` becoming read-only over USB from any host. If `macropad-flash` fails with `OSError: [Errno 30] Read-only filesystem`, the board is still running an *older* `boot.py` without that call -- you need one native (non-tunneled) write to bootstrap it, see below.

### One-time bootstrap (or: recovering a board stuck read-only on *both* sides)

If you've never run `macropad-flash` successfully on this board yet, or `chkdsk`/a normal Windows copy also reports the drive as write-protected (i.e. it's stuck read-only from *every* angle, not just the WSL tunnel), the filesystem itself needs rebuilding:

1. Detach the MacroPad from WSL if it's attached (`usbipd detach --busid <busid>` from Windows, or see "auto-attach keeps stealing the device back" below if it won't stay detached).
2. Unplug the MacroPad, then hold the rotary encoder's push-button while plugging it back in -- `RPI-RP2` should appear.
3. Download [flash_nuke.uf2](https://cdn-learn.adafruit.com/assets/assets/000/101/659/original/flash_nuke.uf2?1618945856) and drag it onto `RPI-RP2`. This **erases the entire flash chip**, including `code.py`/`boot.py`/`lib/` -- a full reflash of just the CircuitPython UF2 (without nuking first) is *not* enough; it preserves the existing (possibly corrupted) filesystem region as-is.
4. The board erases and reboots back into `RPI-RP2` on its own (empty flash falls back to the bootloader automatically -- no need to hold the button again).
5. Drag the CircuitPython `.uf2` for your board (from [circuitpython.org](https://circuitpython.org/board/adafruit_macropad_rp2040/)) onto `RPI-RP2` to reinstall CircuitPython fresh. `CIRCUITPY` reappears, now genuinely empty and writable.
6. From **native Windows** (not through the WSL tunnel -- this step needs to be reliable), copy the CircuitPython library bundle into `CIRCUITPY/lib/` (see [hardware-setup.md](hardware-setup.md#2-install-the-circuitpython-libraries)) and `firmware/boot.py` + `firmware/code.py` into the root. Eject the drive properly before unplugging.
7. Reattach to WSL (`usbipd attach --wsl --busid <busid>`) and confirm the runtime side is now writable:

   ```bash
   uv run python3 -c "
   import serial, time
   ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
   ser.write(b'\x03\x03\r\n'); time.sleep(1); ser.read(3000)
   ser.write(b'import storage\r\n'); time.sleep(0.5); ser.read(500)
   ser.write(b\"print(storage.getmount('/').readonly)\r\n\"); time.sleep(0.5)
   print(ser.read(500))
   ser.close()
   "
   ```

   (Swap `/dev/ttyACM0` for the actual console port -- see the port-listing command in "`macropad-bridge` never prints..." above.) `False` means the fix took -- `macropad-flash` will work reliably from WSL from here on. `True` means `boot.py`'s `storage.remount()` call didn't make it onto the board; redo step 6.

### Auto-attach keeps stealing the device back before you can copy from Windows

If something re-attaches the MacroPad to WSL the instant you detach it (making a native Windows copy impossible), a `usbip-auto-attach` watcher process is doing it. Pause it, do the Windows-side copy, then resume it once you're done:

```bash
sudo macropad-usb-pause    # stop the watcher (needs one-time setup below)
# ... do the Windows-side detach + copy + eject now, no rush ...
sudo macropad-usb-resume   # restart the watcher for normal day-to-day auto-attach
```

`bin/macropad-usb-status` (no `sudo` needed) shows whether the watcher is currently running. One-time setup, so these can run without a password prompt each time:

```bash
sudo cp bin/macropad-usb-pause bin/macropad-usb-resume bin/macropad-usb-status /usr/local/bin/
sudo chmod +x /usr/local/bin/macropad-usb-pause /usr/local/bin/macropad-usb-resume /usr/local/bin/macropad-usb-status
echo "$USER ALL=(root) NOPASSWD: /usr/local/bin/macropad-usb-pause, /usr/local/bin/macropad-usb-resume" | sudo tee /etc/sudoers.d/macropad-usb >/dev/null
sudo chmod 440 /etc/sudoers.d/macropad-usb
sudo visudo -c   # validates the sudoers syntax
```

## I broke `~/.claude/settings.json` or `~/.codex/config.toml`

`macropad-setup` backs up both files before touching them (`<file>.bak-<timestamp>`, next to the original). Restore from the most recent backup and re-run setup.
