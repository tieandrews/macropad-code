# code.py -- runs on the Adafruit MacroPad RP2040 (CircuitPython).
#
# Six jobs, running in the same loop:
#
#   1. Read newline-delimited JSON objects from the USB "data" serial
#      channel (opened in boot.py), sent by the host bridge
#      (macropad/bridge.py). Message shapes:
#          {"key": 3, "color": [255, 0, 0], "pulse": false}  -> set a key's LED
#          {"usage": {"session": {...}, "weekly": {...}}}   -> update the OLED
#          {"model": "Sonnet 4.5"}                           -> update the OLED
#          {"label": "emexams-website"}                      -> update the OLED
#          {"recording": true}                               -> update the OLED
#
#   2. Render the usage message on the built-in OLED as a compact
#      two-line dashboard: one line per metric, each "<label> <bar> <pct>%".
#
#   3. Render a third status line: the selected session's model name
#      (from a {"model": ...} message). A fourth line shows the selected
#      session's project label (from a {"label": ...} message), so you
#      can see which repo the knob is currently pointed at. A fifth,
#      bottom line is a compact status bar -- currently just "MIC: off"/
#      "MIC: REC" (from {"recording": true/false}), on its own line so
#      starting/stopping voice input is unambiguous without touching the
#      model name meanwhile.
#
#   4. Keys 0-5 (top two rows) are "session" keys -- their LEDs track agent
#      state as before. Two effects, layered independently so they never
#      get confused with each other:
#        - `"pulse": true` in a {"key", "color", "pulse"} message (see
#          config/colors.yaml's `pulse:` field, e.g. on the "waiting"
#          state) makes that key breathe brightness -- meant to draw your
#          eye to a status worth actively noticing, regardless of
#          selection.
#        - The rotary encoder cycles which of those six is the *currently
#          selected* session; the selected key is simply rendered
#          brighter than its normal status color (no animation), so
#          selection is visually distinct from a pulsing status. An
#          unconfigured/colorless selected key shows a dim static color
#          instead, so you can still tell the selection landed there.
#          *Pressing* a session key jumps selection straight to it too --
#          e.g. spot a key showing an error color and just press it,
#          rather than dialing the encoder all the way around.
#      Selection changes (from either the encoder or a session-key press)
#      are sent to the host as {"selected": N}.
#
#   5. Keys 6-11 (bottom two rows) are "action" keys. Pressing one sends
#      {"action": N} to the host, which routes it (via tmux) into whatever
#      session is currently selected -- see macropad/sessions.py and
#      config/keymap.yaml's `actions:` section. Keys can still be given
#      local HID macros via KEY_ACTIONS below, which takes priority over
#      forwarding to the host.
#
#   6. Pressing the encoder itself sends {"encoder_press": true} to the
#      host, which toggles voice-to-text capture on/off -- see
#      macropad/voice.py. The board has no microphone of its own; it's
#      purely a remote trigger for the host's recording.
#
# Copy this file to CIRCUITPY/code.py alongside boot.py. See
# docs/hardware-setup.md for the full flashing walkthrough.

import json
import math
import time

import displayio
import terminalio
import usb_cdc
from adafruit_display_text import label
from adafruit_macropad import MacroPad

macropad = MacroPad()
# All per-key LED writes are batched through _render_key_leds()'s single
# macropad.pixels.show() call at the end of every main-loop iteration,
# rather than writing the NeoPixel bus once per key per message/frame.
macropad.pixels.auto_write = False
macropad.pixels.brightness = 0.3

data_serial = usb_cdc.data

# Optional: map key index -> a callable that fires a macro/keystroke.
# Left empty by default; the pad works purely as a status-light grid
# until you fill this in. Takes priority over the default behavior for
# that key (session-key presses do nothing by default; action-key
# presses forward to the host as {"action": N} -- see config/keymap.yaml).
# Example:
#
#   from adafruit_hid.keycode import Keycode
#   KEY_ACTIONS = {
#       0: lambda: macropad.keyboard.send(Keycode.CONTROL, Keycode.C),
#   }
KEY_ACTIONS = {}

SESSION_KEY_COUNT = 6  # keys 0-5 -- must match config/keymap.yaml's layout
ACTION_KEY_START = 6  # keys 6-11

# --- outgoing messages (board -> host) ----------------------------------


def _send_line(msg: dict) -> None:
    if data_serial is None:
        return
    try:
        data_serial.write((json.dumps(msg) + "\n").encode("utf-8"))
    except OSError:
        pass


# --- OLED usage dashboard ---------------------------------------------
#
# Built with raw displayio + adafruit_display_text rather than the
# MacroPad library's display_text() helper, so the exact layout here is
# fully under our control. Bars are drawn with plain ASCII ('#'/'.')
# rather than block-drawing glyphs, since the built-in terminalio font
# only covers ASCII.

BAR_WIDTH = 10
LINE_HEIGHT = 11

_display_group = displayio.Group()
# CircuitPython 9+ uses .root_group; older releases use .show(group).
if hasattr(macropad.display, "root_group"):
    macropad.display.root_group = _display_group
else:
    macropad.display.show(_display_group)

_usage_lines = []
for _i in range(2):
    _line = label.Label(
        terminalio.FONT,
        text="",
        color=0xFFFFFF,
        x=2,
        y=6 + _i * LINE_HEIGHT,
    )
    _display_group.append(_line)
    _usage_lines.append(_line)

# Third line: selected session's model name -- see _apply_model_message()
# below. Fourth line: the selected session's project label (which repo
# the knob is pointed at) -- see _apply_label_message() below. Fifth
# line: a compact bottom status bar, currently just the mic/voice-input
# state -- see _apply_recording_message() below. Kept on its own
# always-visible line rather than overlaying the model line, so pressing
# the encoder to start/stop recording is unambiguous without losing the
# model name meanwhile. Deliberately terse ("MIC: ...") to leave room to
# append more short fields to this same line later.
_status_line = label.Label(
    terminalio.FONT,
    text="",
    color=0xFFFFFF,
    x=2,
    y=6 + 2 * LINE_HEIGHT,
)
_display_group.append(_status_line)

_label_line = label.Label(
    terminalio.FONT,
    text="",
    color=0x00CFFF,
    x=2,
    y=6 + 3 * LINE_HEIGHT,
)
_display_group.append(_label_line)

_bottom_line = label.Label(
    terminalio.FONT,
    text="",
    color=0xFFFFFF,
    x=2,
    y=6 + 4 * LINE_HEIGHT,
)
_display_group.append(_bottom_line)


def _apply_model_message(model_name) -> None:
    _status_line.text = model_name or ""


def _apply_recording_message(is_recording: bool) -> None:
    # The MacroPad's OLED is monochrome -- displayio auto-converts any
    # color to plain black/white by luminance, so a "dim grey"/"dark
    # red" here (as originally tried) doesn't render as a dimmer or
    # differently-hued white, it just falls below the threshold and
    # renders as invisible black text. Always use full white and let
    # the text itself (not color) carry the on/off distinction.
    _bottom_line.text = "MIC: REC" if is_recording else "MIC: off"


def _apply_label_message(project_label) -> None:
    _label_line.text = project_label or ""


_apply_recording_message(False)  # show "MIC: off" from boot, not blank


def _render_bar(pct) -> str:
    pct = max(0, min(100, pct))
    filled = round(BAR_WIDTH * pct / 100)
    return "[" + ("#" * filled) + ("." * (BAR_WIDTH - filled)) + "]" + f" {pct}%"


def _render_metric_line(label_text: str, value: str, pct) -> str:
    if pct is not None:
        return f"{label_text} {_render_bar(pct)}"
    return f"{label_text} {value}"


def _apply_usage_message(usage: dict) -> None:
    session = usage.get("session") or {}
    weekly = usage.get("weekly") or {}

    _usage_lines[0].text = _render_metric_line(
        session.get("label", "SESSION"), session.get("value", ""), session.get("pct")
    )
    _usage_lines[1].text = _render_metric_line(
        weekly.get("label", "WEEKLY"), weekly.get("value", ""), weekly.get("pct")
    )


# --- per-key LEDs -------------------------------------------------------
#
# _key_colors/_key_pulsing cache the last status the host sent for each
# key, so _render_key_leds() has something to render every frame without
# needing to ask the host for it again. Two independent effects:
#
#   - pulse (from colors.yaml's `pulse: true`, e.g. "waiting"): the key
#     breathes brightness on its own status color, whether or not it's
#     selected -- a status worth actively noticing.
#   - selection (from the encoder): the selected key is rendered *brighter*
#     than normal, no animation -- kept as a simple brightness boost
#     specifically so it never reads as the same signal as a pulsing
#     status.
#
# Both can apply to the same key at once (a selected, pulsing key just
# breathes between dim and extra-bright rather than dim-and-normal).

_key_colors = [(0, 0, 0)] * 12
_key_pulsing = [False] * 12


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
    _key_colors[key] = (r, g, b)
    _key_pulsing[key] = bool(msg.get("pulse", False))
    # Actual on-screen brightness is computed fresh every frame by
    # _render_key_leds() -- no need to write macropad.pixels here.


# --- session selection (rotary encoder) ----------------------------------

_selected_key = 0
_encoder_last = macropad.encoder

_PULSE_PERIOD_SECONDS = 1.6
_PULSE_MIN_SCALE = 0.35  # how dim a pulsing key's breath dips to
# Selection contrast comes from both ends at once -- the selected key is
# boosted well past its normal color (usually clamped to full/near-full
# brightness for any mid-to-bright status color) while every other
# session key is dimmed down, rather than relying on a modest boost alone
# to read as "the active one" next to normal-brightness neighbors.
_SELECTED_BRIGHTNESS_SCALE = 2.2  # how much brighter the selected key gets
_UNSELECTED_DIM_SCALE = 0.3  # how much dimmer every other session key gets
# Shown (statically, no pulse) on the selected key only when it has no
# real status color at all yet -- an unconfigured key, or a just-started
# session before its first status push (see sessions.py's start_session).
# Otherwise a colorless selected key would be invisible and you'd have no
# way to tell the encoder selection landed there.
_SELECTED_FLOOR_COLOR = (50, 50, 50)


def _poll_encoder() -> None:
    global _encoder_last, _selected_key
    pos = macropad.encoder
    delta = pos - _encoder_last
    if delta == 0:
        return
    _encoder_last = pos
    _selected_key = (_selected_key + delta) % SESSION_KEY_COUNT
    _send_line({"selected": _selected_key})


def _scale_color(color, factor):
    return tuple(min(255, round(c * factor)) for c in color)


def _render_key_leds() -> None:
    phase = (time.monotonic() % _PULSE_PERIOD_SECONDS) / _PULSE_PERIOD_SECONDS
    pulse_scale = _PULSE_MIN_SCALE + (1 - _PULSE_MIN_SCALE) * (
        0.5 + 0.5 * math.sin(2 * math.pi * phase)
    )
    for key in range(len(macropad.pixels)):
        color = _key_colors[key]
        is_session_key = key < SESSION_KEY_COUNT
        if is_session_key and _key_pulsing[key]:
            color = _scale_color(color, pulse_scale)
        if is_session_key:
            if key == _selected_key:
                color = _scale_color(color, _SELECTED_BRIGHTNESS_SCALE) if any(_key_colors[key]) \
                    else _SELECTED_FLOOR_COLOR
            else:
                color = _scale_color(color, _UNSELECTED_DIM_SCALE)
        macropad.pixels[key] = color
    macropad.pixels.show()


def _poll_encoder_switch() -> None:
    macropad.encoder_switch_debounced.update()
    if macropad.encoder_switch_debounced.pressed:
        _send_line({"encoder_press": True})


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
    if "model" in msg:
        _apply_model_message(msg["model"])
        return
    if "label" in msg:
        _apply_label_message(msg["label"])
        return
    if "recording" in msg:
        _apply_recording_message(msg["recording"])
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
    global _selected_key
    event = macropad.keys.events.get()
    if not event or not event.pressed:
        return
    key = event.key_number
    action = KEY_ACTIONS.get(key)
    if action is not None:
        action()
        return
    if key < SESSION_KEY_COUNT:
        # Jump straight to this session instead of requiring the encoder
        # to be dialed all the way around to it -- e.g. you spot a key
        # showing an error color and just press it, rather than scrolling.
        if key != _selected_key:
            _selected_key = key
            _send_line({"selected": _selected_key})
        return
    if key >= ACTION_KEY_START:
        _send_line({"action": key})


while True:
    _poll_serial()
    _poll_keys()
    _poll_encoder()
    _poll_encoder_switch()
    _render_key_leds()
