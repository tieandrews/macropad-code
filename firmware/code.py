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
#          {"effort": "high"}                                -> update the OLED
#          {"label": "emexams-website"}                      -> update the OLED
#          {"branch": "main"}                                -> update the OLED
#          {"encoder_mode": "session" | "effort"}            -> set what rotation does
#          {"voice_key": 8}                                   -> which action key (if any)
#                                                                 is the mic control, or null
#          {"mic_led": "idle"|"recording"|"pending_review"}  -> mic key's LED state, and
#                                                                 the "*MIC*" marker on line 4
#          {"request_selected": true}    -> re-send {"selected": N} right
#                                            away (a (re)starting bridge has
#                                            no other way to learn the
#                                            board's current selection --
#                                            see serial_link.py's on_connect)
#
#   2. Render the usage message on the built-in OLED as a single line
#      with both metrics together, no bars: "<label>: <pct>%  <label>: <pct>%".
#
#   3. Render a second status line: the selected session's model name and
#      effort level (from {"model": ...} / {"effort": ...} messages,
#      shown together as "Sonnet 4.5 - high"). A third line shows the
#      selected session's project label (from a {"label": ...} message),
#      so you can see which repo the knob is currently pointed at. A
#      fourth line shows that session's current git branch (from
#      a {"branch": ...} message), with a "*MIC*" marker prefixed onto
#      it while voice recording is active or a transcript is pending
#      review (from {"mic_led": ...} -- see _render_branch_line()). The
#      marker disappears once the mic goes idle, so the line spends most
#      of its time just showing the branch. A fifth line's worth of
#      space is left blank below it -- see the OLED usage dashboard
#      section below.
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
#      are sent to the host as {"selected": N}. By default rotation always
#      does this; if the host has set {"encoder_mode": "effort"}, rotation
#      instead sends {"encoder_delta": N} and does *not* change selection
#      -- see config/bridge.yaml's `encoder.rotation_mode`. Session keys
#      still jump selection directly by pressing them, in either mode.
#
#   5. Keys 6-11 (bottom two rows) are "action" keys. Pressing one sends
#      {"action": N} to the host, which routes it (via tmux or herdr, see
#      config/bridge.yaml's `session_backend`) into whatever session is
#      currently selected -- see macropad/sessions.py and
#      config/keymap.yaml's `actions:` section. Keys can still be given
#      local HID macros via KEY_ACTIONS below, which takes priority over
#      forwarding to the host. Unlike session keys, action keys have no
#      dynamic status of their own, but they can still be given a static
#      LED color via the same {"key", "color", "pulse"} message (see job
#      1) -- config/keymap.yaml's `actions:` `color:`/`brightness:`
#      fields, pushed by macropad/bridge.py's _push_action_key_colors().
#      They render with no dimming/pulse/selection effects (those are
#      session-key-only, see _render_key_leds below) -- just the flat
#      color as sent.
#
#   6. The encoder's push-button, and whichever action key (if any) has
#      `type: voice_toggle` in config/keymap.yaml (announced by the host
#      as {"voice_key": N}), both drive voice recording the same way:
#      press sends {"voice_press": true, "source": "encoder"|"key"} and
#      always starts recording immediately (no perceptible delay either
#      way). Release sends {"voice_release": true, "source": ...,
#      "held_ms": N}; the *host* decides from held_ms whether that was a
#      hold-to-talk (stop now) or a quick tap (keep recording, arm
#      toggle-off on the next press) -- see macropad/bridge.py's voice
#      state machine and docs/customization.md. The board has no
#      microphone of its own; it's purely a remote trigger for the
#      host's recording, and doesn't need to know which interpretation
#      applies.
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
# fully under our control.

LINE_HEIGHT = 11

_display_group = displayio.Group()
# CircuitPython 9+ uses .root_group; older releases use .show(group).
if hasattr(macropad.display, "root_group"):
    macropad.display.root_group = _display_group
else:
    macropad.display.show(_display_group)

# First line: both usage metrics together (e.g. "5H 42%  7D 18%") -- see
# _apply_usage_message() below. Second line: selected session's model
# name -- see _apply_model_message() below. Third line: the selected
# session's project label (which repo the knob is pointed at) -- see
# _apply_label_message() below. Fourth line: the selected session's
# current git branch, with a "*MIC*" marker prefixed onto it while voice
# recording is active/pending review -- see _render_branch_line() below.
# The label is often too long to share a line with the branch name
# without running off the 128px-wide display, so the branch gets its own
# line rather than being appended to the label line. That leaves one
# line's worth of the display unused below the branch line -- left blank
# rather than stretching the others out, so a future line has room
# without re-laying everything out again.
_usage_line = label.Label(
    terminalio.FONT,
    text="",
    color=0xFFFFFF,
    x=2,
    y=6,
)
_display_group.append(_usage_line)

_status_line = label.Label(
    terminalio.FONT,
    text="",
    color=0xFFFFFF,
    x=2,
    y=6 + 1 * LINE_HEIGHT,
)
_display_group.append(_status_line)

_label_line = label.Label(
    terminalio.FONT,
    text="",
    color=0x00CFFF,
    x=2,
    y=6 + 2 * LINE_HEIGHT,
)
_display_group.append(_label_line)

_branch_line = label.Label(
    terminalio.FONT,
    text="",
    color=0xFFFFFF,
    x=2,
    y=6 + 3 * LINE_HEIGHT,
)
_display_group.append(_branch_line)


_current_model = ""
_current_effort = ""


def _render_status_line() -> None:
    # Effort is appended to the same line as the model name (rather than
    # a new line) to leave the freed-up line (see the display layout
    # comment above) blank instead of eating into it immediately.
    # terminalio.FONT only covers ASCII, so use a plain hyphen rather
    # than a middle-dot separator.
    if _current_model and _current_effort:
        _status_line.text = f"{_current_model} - {_current_effort}"
    else:
        _status_line.text = _current_model or _current_effort


def _apply_model_message(model_name) -> None:
    global _current_model
    _current_model = model_name or ""
    _render_status_line()


def _apply_effort_message(effort_level) -> None:
    global _current_effort
    _current_effort = effort_level or ""
    _render_status_line()


def _apply_label_message(project_label) -> None:
    _label_line.text = project_label or ""


_current_branch = ""


def _render_branch_line() -> None:
    # "*MIC*" is prefixed onto the branch line while recording or
    # awaiting a pending-review confirm tap (see _mic_led_state below,
    # set from the host's {"mic_led": ...} messages) -- both states
    # share the same marker; the mic key's own LED color (blue vs amber)
    # is what actually distinguishes recording from pending-review, this
    # line just needs to say "something mic-related is happening".
    # Monochrome OLED (see the color-vs-luminance note in
    # _apply_led_message/firmware history) -- text presence/absence
    # carries the signal, not color, same reasoning as the old MIC line.
    mic_marker = "*MIC* " if _mic_led_state in ("recording", "pending_review") else ""
    _branch_line.text = mic_marker + _current_branch


def _apply_branch_message(branch_name) -> None:
    global _current_branch
    _current_branch = branch_name or ""
    _render_branch_line()


def _render_metric(label_text: str, value: str, pct) -> str:
    # A raw percentage (no bar) when we have one; falls back to whatever
    # `value` the host sent otherwise -- a token count for the
    # local-estimate source (see usage.py's _payload_from_local_estimate,
    # config/usage.yaml's `source: claude_local`) with no budget
    # configured to compute a percentage against, or "?" for the
    # claude_pty source if it couldn't parse that particular metric out
    # of the /usage panel this poll.
    if pct is not None:
        return f"{label_text}:{pct}%"
    return f"{label_text}:{value}"


def _apply_usage_message(usage: dict) -> None:
    session = usage.get("session") or {}
    weekly = usage.get("weekly") or {}

    session_part = _render_metric(
        session.get("label", "SESSION"), session.get("value", ""), session.get("pct")
    )
    weekly_part = _render_metric(
        weekly.get("label", "WEEKLY"), weekly.get("value", ""), weekly.get("pct")
    )
    _usage_line.text = f"{session_part}  {weekly_part}"


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

# "session" (default): rotation cycles _selected_key, as always.
# "effort": rotation instead sends {"encoder_delta": N} to the host,
# which cycles the *selected* session's Claude Code effort level -- see
# config/bridge.yaml's encoder.rotation_mode. Set by the host on connect
# (and periodically re-sent -- see bridge.py's _model_loop), not a local
# board setting, since the board has no config file of its own.
_encoder_mode = "session"


def _apply_encoder_mode_message(mode) -> None:
    global _encoder_mode
    if mode in ("session", "effort"):
        _encoder_mode = mode


# --- mic key/LED (voice_toggle action key) ------------------------------
#
# The board doesn't know which key (if any) is `type: voice_toggle` --
# the host announces it via {"voice_key": N} (or null for none) so
# _poll_keys() knows to track press/release for that key instead of
# firing the normal one-shot {"action": N} on press. Independent of
# _key_colors -- the mic key's LED is host-driven status (idle/
# recording/pending_review), not the per-status color grid other action
# keys use, so it's rendered as an override in _render_key_leds() rather
# than going through _apply_led_message().
_voice_key = None
_mic_led_state = "idle"  # "idle" | "recording" | "pending_review"

_MIC_IDLE_COLOR = (0, 0, 40)  # very dim blue -- marks the key as "special" even at rest
_MIC_RECORDING_COLOR = (40, 40, 255)  # bright blue -- actively capturing audio
_MIC_PENDING_REVIEW_COLOR = (255, 140, 0)  # amber -- transcript sitting in the pane, needs a confirm tap


def _apply_voice_key_message(key) -> None:
    global _voice_key
    _voice_key = key if isinstance(key, int) and ACTION_KEY_START <= key < 12 else None


def _apply_mic_led_message(state) -> None:
    global _mic_led_state
    if state in ("idle", "recording", "pending_review"):
        _mic_led_state = state
        _render_branch_line()  # the "*MIC*" marker on line 5 tracks this too


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
    if _encoder_mode == "effort":
        _send_line({"encoder_delta": delta})
        return
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
        if key == _voice_key:
            # Overrides whatever status color that key would otherwise
            # show -- the mic key's LED is host-driven recording state,
            # not a per-key status color, so it always wins here.
            if _mic_led_state == "recording":
                color = _scale_color(_MIC_RECORDING_COLOR, pulse_scale)
            elif _mic_led_state == "pending_review":
                color = _MIC_PENDING_REVIEW_COLOR
            else:
                color = _MIC_IDLE_COLOR
        macropad.pixels[key] = color
    macropad.pixels.show()


_encoder_press_started_at = None


def _poll_encoder_switch() -> None:
    global _encoder_press_started_at
    macropad.encoder_switch_debounced.update()
    if macropad.encoder_switch_debounced.pressed:
        _encoder_press_started_at = time.monotonic()
        _send_line({"voice_press": True, "source": "encoder"})
    elif macropad.encoder_switch_debounced.released:
        held_ms = 0
        if _encoder_press_started_at is not None:
            held_ms = round((time.monotonic() - _encoder_press_started_at) * 1000)
        _encoder_press_started_at = None
        _send_line({"voice_release": True, "source": "encoder", "held_ms": held_ms})


# --- serial dispatch ------------------------------------------------------

_line_buffer = b""


def _handle_message(raw_line: bytes) -> None:
    try:
        msg = json.loads(raw_line.decode("utf-8"))
    except (ValueError, UnicodeError):
        return
    if not isinstance(msg, dict):
        return

    if "request_selected" in msg:
        # Sent by a (re)starting bridge that has no way to know the
        # board's current selection otherwise -- we only ever send
        # {"selected": N} on an actual rotation/press, never
        # proactively. See serial_link.py's on_connect / bridge.py's
        # _on_serial_connect().
        _send_line({"selected": _selected_key})
        return
    if "usage" in msg:
        _apply_usage_message(msg["usage"])
        return
    if "model" in msg:
        _apply_model_message(msg["model"])
        return
    if "effort" in msg:
        _apply_effort_message(msg["effort"])
        return
    if "encoder_mode" in msg:
        _apply_encoder_mode_message(msg["encoder_mode"])
        return
    if "voice_key" in msg:
        _apply_voice_key_message(msg["voice_key"])
        return
    if "mic_led" in msg:
        _apply_mic_led_message(msg["mic_led"])
        return
    if "label" in msg:
        _apply_label_message(msg["label"])
        return
    if "branch" in msg:
        _apply_branch_message(msg["branch"])
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


_key_press_started_at = {}  # key_number -> time.monotonic() at press, for the voice key only


def _poll_keys() -> None:
    global _selected_key
    event = macropad.keys.events.get()
    if not event:
        return
    key = event.key_number

    if key == _voice_key:
        # The voice key reports press/release timing instead of the
        # normal one-shot {"action": N} on press -- see the module
        # docstring's job 6 and macropad/bridge.py's voice state machine.
        if event.pressed:
            _key_press_started_at[key] = time.monotonic()
            _send_line({"voice_press": True, "source": "key", "key": key})
        elif event.released:
            held_ms = 0
            started = _key_press_started_at.pop(key, None)
            if started is not None:
                held_ms = round((time.monotonic() - started) * 1000)
            _send_line({"voice_release": True, "source": "key", "key": key, "held_ms": held_ms})
        return

    if not event.pressed:
        return
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
