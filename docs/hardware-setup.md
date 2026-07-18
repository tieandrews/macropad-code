# Hardware setup

## What you need

- [Adafruit MacroPad RP2040](https://www.adafruit.com/product/5128) (starter kit recommended -- it includes switches, keycaps, and the enclosure)
- A data-capable USB-C cable (some cables are charge-only and won't work)

## 1. Install CircuitPython

1. Download the latest MacroPad RP2040 `.uf2` build from the [CircuitPython downloads page](https://circuitpython.org/board/adafruit_macropad_rp2040/).
2. Put the board into bootloader mode: hold down the rotary encoder's push-button while plugging in the USB cable. A drive named `RPI-RP2` should appear.
3. Drag the `.uf2` file onto `RPI-RP2`. The board reboots and a new drive named `CIRCUITPY` appears.

## 2. Install the CircuitPython libraries

Download the [Adafruit CircuitPython library bundle](https://circuitpython.org/libraries) matching your CircuitPython version, then copy these into `CIRCUITPY/lib/`:

- `adafruit_macropad.mpy`
- `adafruit_debouncer.mpy`
- `adafruit_ticks.mpy`
- `adafruit_simple_text_display.mpy`
- `neopixel.mpy`
- `adafruit_hid/` (folder)
- `adafruit_midi/` (folder)
- `adafruit_display_text/` (folder)

This is the full dependency list of `adafruit_macropad` itself, not just what `code.py` imports directly -- missing any one of these makes `MacroPad()` raise an `ImportError` as soon as `code.py` starts. If that happens, the OLED falls back to showing CircuitPython's default console (a version banner + "Press any key to enter the REPL. Use CTRL-D to reload.") instead of going blank, since `code.py` never got far enough to take over the display. That's not bootloader mode -- the drive is still named `CIRCUITPY`, not `RPI-RP2` -- it just means `code.py` crashed. Connect a serial terminal to the console port to see the actual traceback if this happens.

## 3. Copy this repo's firmware

Copy both files from [`firmware/`](../firmware/) to the root of the `CIRCUITPY` drive:

```
firmware/boot.py -> CIRCUITPY/boot.py
firmware/code.py -> CIRCUITPY/code.py
```

`boot.py` only runs at power-on, so **unplug and replug the MacroPad** after copying it (or after ever editing it again) for the change to take effect. `code.py` reloads automatically whenever you save it.

For this very first copy, drag the files onto `CIRCUITPY` directly (from Windows Explorer if you're on WSL with USB passthrough -- see the note below). For every update *after* this one, use `uv sync --extra firmware && uv run macropad-flash` instead, which pushes both files over the serial connection rather than the mass-storage drive -- more reliable, especially on WSL, and works from inside WSL itself with no Windows detour. See [troubleshooting.md](troubleshooting.md#updating-firmware-over-wslusb-write-protect-errors-reverted-files) for why.

## 4. Verify

With both files in place, the OLED should go completely blank (it has no header text of its own -- that's expected) and the keys should go dark (all LEDs off) until the bridge starts sending data. Run `uv run macropad-bridge` (see [running-the-bridge.md](running-the-bridge.md)) and confirm the console prints a line like:

```
[macropad-bridge] connected to /dev/ttyACM1
```

(or `/dev/cu.usbmodem*` on macOS, `COMx` on Windows). If it never connects, see [troubleshooting.md](troubleshooting.md). If usage display is enabled (see [customization.md](customization.md)), the OLED should fill in with token counts and bar graphs within `poll_interval_seconds`.

## Optional: real macros

`code.py` scans key presses but doesn't send any local keystrokes by default -- out of the box the top two rows (0-5) are a pure status-light grid, and the bottom two rows (6-11) forward presses to the host as `{"action": N}` messages, which are a no-op until you configure `config/keymap.yaml`'s `actions:` section (see [customization.md](customization.md)). If you'd rather have a key send a real local keystroke instead of going through the host/tmux pipeline, fill in the `KEY_ACTIONS` dict near the top of `code.py` with `adafruit_hid` keycodes -- it takes priority over both behaviors above for whichever key you map there.
