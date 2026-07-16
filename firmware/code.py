# code.py -- runs on the Adafruit MacroPad RP2040 (CircuitPython).
#
# Two jobs, running in the same loop:
#
#   1. Read newline-delimited JSON objects from the USB "data" serial
#      channel (opened in boot.py) and set NeoPixel colors from them.
#      Sent by the host bridge (macropad/bridge.py). Message shape:
#          {"key": 3, "color": [255, 0, 0]}
#
#   2. Scan the 12 keys and the encoder for presses, show the pressed
#      key's label on the OLED, and optionally fire a keystroke/macro.
#      This is intentionally minimal -- it just proves the pad is alive
#      and gives you a hook point for real macros. Extend the
#      KEY_ACTIONS table below to send real keystrokes via
#      adafruit_hid, or leave keys unmapped to use the pad purely as a
#      status-light grid.
#
# Copy this file to CIRCUITPY/code.py alongside boot.py. See
# docs/hardware-setup.md for the full flashing walkthrough.

import json

import usb_cdc
from adafruit_macropad import MacroPad

macropad = MacroPad()
macropad.pixels.auto_write = True
macropad.pixels.brightness = 0.3

data_serial = usb_cdc.data

# Optional: map key index -> a callable that fires a macro/keystroke.
# Left empty by default; the pad works purely as a status-light grid
# until you fill this in. Example:
#
#   from adafruit_hid.keycode import Keycode
#   KEY_ACTIONS = {
#       0: lambda: macropad.keyboard.send(Keycode.CONTROL, Keycode.C),
#   }
KEY_ACTIONS = {}

_line_buffer = b""


def _apply_led_message(raw_line: bytes) -> None:
    try:
        msg = json.loads(raw_line.decode("utf-8"))
    except (ValueError, UnicodeError):
        return

    key = msg.get("key")
    color = msg.get("color")
    if key is None or color is None:
        return
    if not (0 <= key < len(macropad.pixels)):
        return
    try:
        r, g, b = (int(c) & 0xFF for c in color)
    except (TypeError, ValueError):
        return
    macropad.pixels[key] = (r, g, b)


def _poll_serial() -> None:
    global _line_buffer
    if data_serial is None or data_serial.in_waiting == 0:
        return
    _line_buffer += data_serial.read(data_serial.in_waiting)
    while b"\n" in _line_buffer:
        line, _line_buffer = _line_buffer.split(b"\n", 1)
        if line:
            _apply_led_message(line)


def _poll_keys() -> None:
    event = macropad.keys.events.get()
    if not event or not event.pressed:
        return
    key = event.key_number
    action = KEY_ACTIONS.get(key)
    if action is not None:
        action()


while True:
    _poll_serial()
    _poll_keys()
