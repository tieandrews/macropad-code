# code.py -- runs on the Adafruit MacroPad RP2040 (CircuitPython).
#
# Three jobs, running in the same loop:
#
#   1. Read newline-delimited JSON objects from the USB "data" serial
#      channel (opened in boot.py), sent by the host bridge
#      (macropad/bridge.py). Two message shapes:
#          {"key": 3, "color": [255, 0, 0]}                 -> set a key's LED
#          {"usage": {"session": {...}, "weekly": {...}}}   -> update the OLED
#
#   2. Render the usage message on the built-in OLED as a small dashboard:
#      a label + value line and a bar-graph line per metric.
#
#   3. Scan the 12 keys and the encoder for presses, and optionally fire a
#      keystroke/macro. This is intentionally minimal -- it just proves
#      the pad is alive and gives you a hook point for real macros. Extend
#      the KEY_ACTIONS table below to send real keystrokes via
#      adafruit_hid, or leave keys unmapped to use the pad purely as a
#      status-light grid.
#
# Copy this file to CIRCUITPY/code.py alongside boot.py. See
# docs/hardware-setup.md for the full flashing walkthrough.

import json

import displayio
import terminalio
import usb_cdc
from adafruit_display_text import label
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

# --- OLED usage dashboard ---------------------------------------------
#
# Built with raw displayio + adafruit_display_text rather than the
# MacroPad library's display_text() helper, so the exact layout here is
# fully under our control. Bars are drawn with plain ASCII ('#'/'.')
# rather than block-drawing glyphs, since the built-in terminalio font
# only covers ASCII.

BAR_WIDTH = 12
LINE_HEIGHT = 11

_display_group = displayio.Group()
# CircuitPython 9+ uses .root_group; older releases use .show(group).
if hasattr(macropad.display, "root_group"):
    macropad.display.root_group = _display_group
else:
    macropad.display.show(_display_group)

_usage_lines = []
for _i in range(5):
    _line = label.Label(
        terminalio.FONT,
        text="",
        color=0xFFFFFF,
        x=2,
        y=6 + _i * LINE_HEIGHT,
    )
    _display_group.append(_line)
    _usage_lines.append(_line)

_usage_lines[0].text = "AGENT USAGE"


def _render_bar(pct) -> str:
    pct = max(0, min(100, pct))
    filled = round(BAR_WIDTH * pct / 100)
    return "[" + ("#" * filled) + ("." * (BAR_WIDTH - filled)) + "]" + f" {pct}%"


def _apply_usage_message(usage: dict) -> None:
    session = usage.get("session") or {}
    weekly = usage.get("weekly") or {}

    s_label = session.get("label", "SESSION")
    s_value = session.get("value", "")
    s_pct = session.get("pct")
    _usage_lines[1].text = f"{s_label}: {s_value}"
    _usage_lines[2].text = _render_bar(s_pct) if s_pct is not None else ""

    w_label = weekly.get("label", "WEEKLY")
    w_value = weekly.get("value", "")
    w_pct = weekly.get("pct")
    _usage_lines[3].text = f"{w_label}: {w_value}"
    _usage_lines[4].text = _render_bar(w_pct) if w_pct is not None else ""


# --- per-key LEDs -------------------------------------------------------

def _apply_led_message(msg: dict) -> None:
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


# --- serial dispatch ------------------------------------------------------

_line_buffer = b""


def _handle_message(raw_line: bytes) -> None:
    try:
        msg = json.loads(raw_line.decode("utf-8"))
    except (ValueError, UnicodeError):
        return
    if not isinstance(msg, dict):
        return

    if "usage" in msg:
        _apply_usage_message(msg["usage"])
        return
    _apply_led_message(msg)


def _poll_serial() -> None:
    global _line_buffer
    if data_serial is None or data_serial.in_waiting == 0:
        return
    _line_buffer += data_serial.read(data_serial.in_waiting)
    while b"\n" in _line_buffer:
        line, _line_buffer = _line_buffer.split(b"\n", 1)
        if line:
            _handle_message(line)


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
