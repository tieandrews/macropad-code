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

- `adafruit_macropad`
- `neopixel`
- `adafruit_hid`
- `adafruit_display_text`

## 3. Copy this repo's firmware

Copy both files from [`firmware/`](../firmware/) to the root of the `CIRCUITPY` drive:

```
firmware/boot.py -> CIRCUITPY/boot.py
firmware/code.py -> CIRCUITPY/code.py
```

`boot.py` only runs at power-on, so **unplug and replug the MacroPad** after copying it (or after ever editing it again) for the change to take effect. `code.py` reloads automatically whenever you save it.

## 4. Verify

With both files in place, the OLED should show the default MacroPad splash and the keys should go dark (all LEDs off) until the bridge starts sending colors. Run `macropad-bridge` (see [running-the-bridge.md](running-the-bridge.md)) and confirm the console prints a line like:

```
[macropad-bridge] connected to /dev/ttyACM1
```

(or `/dev/cu.usbmodem*` on macOS, `COMx` on Windows). If it never connects, see [troubleshooting.md](troubleshooting.md).

## Optional: real macros

`code.py` scans key presses but doesn't send any keystrokes by default -- out of the box this is a pure status-light grid. If you also want the keys to act as a real macro pad, fill in the `KEY_ACTIONS` dict near the top of `code.py` with `adafruit_hid` keycodes; see the comment in that file for an example.
