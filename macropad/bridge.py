"""macropad-bridge: the long-running daemon that owns the MacroPad's serial
connection.

Hook scripts (macropad/hooks/*) never talk to the serial port directly --
they send a small JSON message to this process's local TCP socket instead.
That keeps exactly one thing responsible for the (fragile, single-owner)
USB connection, and means hooks never block an agent session waiting on
hardware.

This process also owns the optional usage-display poller: a background
thread that periodically reads local Claude Code usage (see usage.py) and
pushes it straight to the MacroPad's OLED over the same serial link --
independent of the per-key LED status traffic coming from hooks.

It's also the other end of the board's board->host messages (see
firmware/code.py):

  - `{"selected": N}` when the rotary encoder changes which session key
    (0-5) is selected -- also triggers an immediate model-name refresh
    for the OLED, rather than waiting for the next poll.
  - `{"action": N}` when an action key (6-11) is pressed -- routed into
    the selected session's tmux pane via macropad/sessions.py.
  - `{"encoder_press": true}` when the encoder's push-button is
    pressed -- toggles voice-to-text recording on/off (macropad/voice.py).

A second background poller periodically snapshots the selected
session's tmux pane to detect which Claude Code model it's using, and
pushes `{"model": "..."}` to the OLED -- independent of the usage-display
poller above.

Run with: macropad-bridge   (installed via pyproject.toml console_scripts)
or:       python -m macropad.bridge
"""
from __future__ import annotations

import json
import socketserver
import threading

from . import config, usage
from .serial_link import ReconnectingSerial

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999
DEFAULT_MODEL_POLL_INTERVAL_SECONDS = 5


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        self.server.on_message(msg)  # type: ignore[attr-defined]


class Bridge:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 baudrate: int = 115200, retry_seconds: float = 2.0,
                 model_poll_interval_seconds: float = DEFAULT_MODEL_POLL_INTERVAL_SECONDS,
                 voice_settings: dict | None = None):
        self.host = host
        self.port = port
        self.link = ReconnectingSerial(
            baudrate=baudrate, retry_seconds=retry_seconds, on_line=self._on_board_line
        )
        self._server = socketserver.ThreadingTCPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._server.on_message = self._on_message  # type: ignore[attr-defined]
        self._usage_stop = threading.Event()
        self._usage_thread: threading.Thread | None = None
        self._selected_key = 0

        self._model_poll_interval = model_poll_interval_seconds
        self._model_stop = threading.Event()
        self._model_thread: threading.Thread | None = None
        self._last_sent_model: str | None = "__unset__"  # force one send on first poll

        self._voice_settings = voice_settings or {}
        self._recording = False
        self._recorder = None
        self._model_index: dict[int, int] = {}  # session key -> cycle_model position

    def _on_message(self, msg: dict) -> None:
        key = msg.get("key")
        state = msg.get("state")
        if key is None or state is None:
            return
        color = config.color_for_state(state)
        pulse = config.pulse_for_state(state)
        payload = json.dumps({"key": key, "color": color, "pulse": pulse})
        ok = self.link.write_line(payload)
        status = "sent" if ok else "dropped (MacroPad not connected)"
        print(f"[macropad-bridge] key={key} state={state} color={color} "
              f"pulse={pulse} -> {status}", flush=True)

    def _on_board_line(self, line: str) -> None:
        """Handles a message the *board* sent us: encoder selection
        changes and action-key presses -- see firmware/code.py."""
        from . import sessions

        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return

        if "selected" in msg:
            try:
                self._selected_key = int(msg["selected"])
            except (TypeError, ValueError):
                return
            print(f"[macropad-bridge] selected session key={self._selected_key}", flush=True)
            self._send_selected_label()
            self._poll_model_once()  # don't make the user wait for the next scheduled poll
            sessions.sync_follow_session(self._selected_key)
            return

        if "action" in msg:
            try:
                action_key = int(msg["action"])
            except (TypeError, ValueError):
                return
            self._dispatch_action(action_key)
            return

        if "encoder_press" in msg:
            self._on_encoder_press()

    def _dispatch_action(self, action_key: int) -> None:
        entry = (config.load_keymap().get("actions") or {}).get(action_key)
        if not entry:
            print(f"[macropad-bridge] action key={action_key} pressed but nothing configured "
                  "for it in config/keymap.yaml's `actions:` -- see docs/customization.md",
                  flush=True)
            return

        label = entry.get("label", f"action{action_key}")
        if entry.get("type") == "cycle_model":
            self._dispatch_cycle_model(label, entry)
            return

        from . import sessions

        ok = sessions.send_to_session(
            self._selected_key, entry.get("send_keys", ""), entry.get("enter", True)
        )
        status = "sent" if ok else "failed (no tmux session running for the selected key?)"
        print(f"[macropad-bridge] action='{label}' -> session key={self._selected_key} {status}",
              flush=True)

    def _dispatch_cycle_model(self, label: str, entry: dict) -> None:
        from . import sessions

        models = entry.get("models") or ["sonnet", "opus", "haiku"]
        key = self._selected_key
        index = (self._model_index.get(key, -1) + 1) % len(models)
        self._model_index[key] = index
        next_model = models[index]

        ok = sessions.send_to_session(key, f"/model {next_model}", True)
        status = "sent" if ok else "failed (no tmux session running for the selected key?)"
        print(f"[macropad-bridge] action='{label}' -> session key={key} switching to "
              f"'{next_model}' {status}", flush=True)
        if ok:
            self._poll_model_once()  # refresh the OLED once the switch has had a moment to land

    def _on_encoder_press(self) -> None:
        if not self._voice_settings.get("enabled", False):
            print("[macropad-bridge] encoder pressed but voice input is disabled -- set "
                  "voice.enabled: true in config/bridge.yaml to turn it on. See "
                  "docs/customization.md.", flush=True)
            return

        from . import voice

        if not self._recording:
            recorder = voice.Recorder()
            if not recorder.start():
                print("[macropad-bridge] couldn't start voice recording (no microphone / "
                      "missing dependencies -- see docs/customization.md)", flush=True)
                return
            self._recorder = recorder
            self._recording = True
            self.link.write_line(json.dumps({"recording": True}))
            print("[macropad-bridge] voice recording started", flush=True)
            return

        self._recording = False
        self.link.write_line(json.dumps({"recording": False}))
        recorder, self._recorder = self._recorder, None
        print("[macropad-bridge] voice recording stopped, transcribing...", flush=True)
        threading.Thread(target=self._finish_voice_capture, args=(recorder,), daemon=True).start()

    def _finish_voice_capture(self, recorder) -> None:
        from . import sessions, voice

        audio = recorder.stop()
        text = voice.transcribe(audio, self._voice_settings.get("whisper_model", "base"))
        if not text:
            print("[macropad-bridge] voice capture produced no usable transcription", flush=True)
            return

        ok = sessions.send_to_session(self._selected_key, text, True)
        status = "sent" if ok else "failed (no tmux session running for the selected key?)"
        print(f"[macropad-bridge] voice -> session key={self._selected_key} {status}: "
              f"\"{text}\"", flush=True)

    def _send_selected_label(self) -> None:
        """Pushes the selected session's configured label (config/keymap.yaml's
        `keys:` entry) to the OLED, so you can see which repo/project the
        knob is currently pointed at -- independent of (and shown below)
        the model-name line. `None`/blank for an unconfigured key."""
        keys = config.load_keymap().get("keys") or {}
        entry = keys.get(self._selected_key)
        label = entry.get("label") if entry else None
        self.link.write_line(json.dumps({"label": label}))

    def _poll_model_once(self) -> None:
        from . import sessions

        model = sessions.detect_model(self._selected_key)
        if model == self._last_sent_model:
            return
        self._last_sent_model = model
        self.link.write_line(json.dumps({"model": model}))

    def _model_loop(self) -> None:
        while not self._model_stop.is_set():
            try:
                self._poll_model_once()
                # Piggyback the selected-session label on this same loop --
                # cheap, and self-heals the one-shot pushes in
                # serve_forever()/_on_board_line() if they raced the
                # serial connection still (re)connecting.
                self._send_selected_label()
                from . import sessions

                sessions.sync_follow_session(self._selected_key)
            except Exception as exc:  # best-effort: never let model polling kill the bridge
                print(f"[macropad-bridge] model poll failed: {exc}", flush=True)
            self._model_stop.wait(self._model_poll_interval)

    def start_model_poller(self) -> None:
        self._model_thread = threading.Thread(target=self._model_loop, daemon=True)
        self._model_thread.start()

    def _usage_loop(self, settings: dict) -> None:
        interval = settings.get("poll_interval_seconds", 60)
        while not self._usage_stop.is_set():
            try:
                payload = usage.build_display_payload(settings)
                fallback_from = payload.pop("_fallback_from", None)
                ok = self.link.write_line(json.dumps(payload))
                if ok:
                    s = payload["usage"]["session"]["value"]
                    w = payload["usage"]["weekly"]["value"]
                    suffix = (
                        f" (fell back from {fallback_from} -- it returned no result "
                        "this poll, see docs/customization.md for expected causes)"
                        if fallback_from else ""
                    )
                    print(f"[macropad-bridge] usage session={s} weekly={w} -> sent{suffix}", flush=True)
            except Exception as exc:  # best-effort: never let usage polling kill the bridge
                print(f"[macropad-bridge] usage poll failed: {exc}", flush=True)
            self._usage_stop.wait(interval)

    def start_usage_poller(self) -> None:
        settings = config.load_usage_settings()
        if not settings.get("enabled", False):
            return

        source = settings.get("source", "local_estimate")
        interval = settings.get("poll_interval_seconds", 60)
        if source == "claude_pty":
            if interval < 300:
                print(f"[macropad-bridge] warning: usage source is claude_pty but "
                      f"poll_interval_seconds is {interval}s -- this launches a full "
                      "claude process every poll; consider 300s+. See "
                      "config/usage.yaml / docs/customization.md.", flush=True)
            try:
                import pyte  # noqa: F401
            except ImportError:
                print("[macropad-bridge] warning: usage source is claude_pty but the "
                      "optional 'pyte' package isn't installed (uv sync --extra pty) "
                      "-- falling back to a cruder ANSI stripper.", flush=True)

        self._usage_thread = threading.Thread(
            target=self._usage_loop, args=(settings,), daemon=True
        )
        self._usage_thread.start()
        print(f"[macropad-bridge] usage display enabled (source={source}, "
              f"every {interval}s)", flush=True)

    def serve_forever(self) -> None:
        print(f"[macropad-bridge] listening on {self.host}:{self.port}", flush=True)
        self._send_selected_label()  # push key 0's label immediately, don't wait for a rotation
        from . import sessions

        sessions.sync_follow_session(self._selected_key)
        self.start_usage_poller()
        self.start_model_poller()
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._usage_stop.set()
            self._model_stop.set()
            self._server.server_close()
            self.link.close()


def main() -> None:
    settings = config.load_bridge_settings()
    serial_settings = settings.get("serial", {})
    bridge = Bridge(
        host=settings.get("host", DEFAULT_HOST),
        port=settings.get("port", DEFAULT_PORT),
        baudrate=serial_settings.get("baudrate", 115200),
        retry_seconds=serial_settings.get("retry_seconds", 2.0),
        model_poll_interval_seconds=settings.get(
            "model_poll_interval_seconds", DEFAULT_MODEL_POLL_INTERVAL_SECONDS
        ),
        voice_settings=settings.get("voice", {}),
    )
    bridge.serve_forever()


if __name__ == "__main__":
    main()
