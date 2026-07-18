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
                 voice_settings: dict | None = None, encoder_settings: dict | None = None):
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
        self._stt = None
        self._streamed_chars = 0
        self._model_index: dict[int, int] = {}  # session key -> cycle_model position
        self._effort_index: dict[int, int] = {}  # session key -> cycle_effort position
        self._last_sent_effort: dict[int, str] = {}  # session key -> last /effort level sent
        encoder_settings = encoder_settings or {}
        rotation_mode = encoder_settings.get("rotation_mode", "session")
        self._encoder_rotation_mode = rotation_mode if rotation_mode in ("session", "effort") else "session"

    def _on_message(self, msg: dict) -> None:
        if msg.get("reload_config"):
            self._reload_config()
            return

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

    def _reload_config(self) -> None:
        """Re-reads config/bridge.yaml's `voice`/`encoder` settings into
        the running bridge -- everything else (colors.yaml, keymap.yaml)
        is already read fresh on every access (see macropad/config.py),
        so this only matters for the two things cached at construction
        time. Triggered by the web UI's "Apply to pad" (see webui.py) so
        changes take effect without a manual bridge restart."""
        settings = config.load_bridge_settings()
        self._voice_settings = settings.get("voice", {})
        encoder_settings = settings.get("encoder", {}) or {}
        rotation_mode = encoder_settings.get("rotation_mode", "session")
        self._encoder_rotation_mode = rotation_mode if rotation_mode in ("session", "effort") else "session"
        self._push_encoder_mode()
        print("[macropad-bridge] config reloaded (voice + encoder settings)", flush=True)

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
            self._push_effort_to_oled()
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
            triggers = self._voice_settings.get("triggers") or {}
            if triggers.get("encoder", True):
                self._toggle_voice(trigger="encoder press")
            return

        if "encoder_delta" in msg:
            if self._encoder_rotation_mode != "effort":
                return  # firmware shouldn't send this outside effort mode, but ignore defensively
            try:
                delta = int(msg["encoder_delta"])
            except (TypeError, ValueError):
                return
            if delta == 0:
                return
            entry = (config.load_bridge_settings().get("encoder") or {})
            levels = entry.get("effort_levels") or ["low", "medium", "high", "max"]
            self._advance_effort(self._selected_key, levels, step=1 if delta > 0 else -1,
                                  trigger="encoder rotation")
            return

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

        if entry.get("type") == "cycle_effort":
            levels = entry.get("levels") or ["low", "medium", "high", "max"]
            self._advance_effort(self._selected_key, levels, step=1,
                                  trigger=f"action key {action_key} ({label})")
            return

        if entry.get("type") == "voice_toggle":
            self._toggle_voice(trigger=f"action key {action_key} ({label})")
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

    def _advance_effort(self, key: int, levels: list, step: int, trigger: str) -> None:
        """Shared by the cycle_effort action key and encoder rotation (in
        effort mode) -- advances `key`'s effort-level position by `step`
        and sends `/effort <level>` into its session. There's no way to
        detect Claude Code's *actual* current effort level from the tmux
        pane (unlike model name -- see sessions.detect_model()), so
        self._last_sent_effort only reflects what this bridge itself has
        sent since it started, not ground truth."""
        from . import sessions

        if not levels:
            return
        index = (self._effort_index.get(key, -1) + step) % len(levels)
        self._effort_index[key] = index
        next_level = levels[index]

        ok = sessions.send_to_session(key, f"/effort {next_level}", True)
        status = "sent" if ok else "failed (no tmux session running for the selected key?)"
        print(f"[macropad-bridge] {trigger} -> session key={key} switching effort to "
              f"'{next_level}' {status}", flush=True)
        if ok:
            self._last_sent_effort[key] = next_level
            if key == self._selected_key:
                self._push_effort_to_oled()

    def _toggle_voice(self, trigger: str = "voice toggle") -> None:
        if not self._voice_settings.get("enabled", False):
            print("[macropad-bridge] voice toggle but voice input is disabled -- set "
                  "voice.enabled: true in config/bridge.yaml. See docs/customization.md.",
                  flush=True)
            return

        if not self._recording:
            self._start_voice(trigger)
        else:
            self._stop_voice(trigger)

    def _start_voice(self, trigger: str) -> None:
        from . import voice
        from .stt import create_backend

        try:
            self._stt = create_backend(self._voice_settings)
        except Exception as exc:
            print(f"[macropad-bridge] voice backend setup failed: {exc}", flush=True)
            self._stt = None
            return

        self._streamed_chars = 0
        backend = self._voice_settings.get("backend", "local_whisper")
        streaming = getattr(self._stt, "streaming", False)

        if streaming:
            from . import sessions

            session_key = self._selected_key

            def on_delta(delta: str) -> None:
                if not delta:
                    return
                sessions.send_to_session(session_key, delta, send_enter=False)
                self._streamed_chars += len(delta)

            def on_completed(transcript: str) -> None:
                remainder = transcript[self._streamed_chars :]
                if remainder:
                    sessions.send_to_session(session_key, remainder, send_enter=False)
                    self._streamed_chars += len(remainder)

            if not self._stt.start(on_delta=on_delta, on_completed=on_completed):
                print(f"[macropad-bridge] couldn't start streaming STT ({backend})",
                      flush=True)
                self._stt.close()
                self._stt = None
                return

            recorder = voice.Recorder()
            if not recorder.start(on_chunk=self._stt.feed_audio):
                print("[macropad-bridge] couldn't start voice recording (no microphone / "
                      "missing dependencies -- see docs/customization.md)", flush=True)
                self._stt.close()
                self._stt = None
                return
        else:
            recorder = voice.Recorder()
            if not recorder.start():
                print("[macropad-bridge] couldn't start voice recording (no microphone / "
                      "missing dependencies -- see docs/customization.md)", flush=True)
                self._stt.close()
                self._stt = None
                return

        self._recorder = recorder
        self._recording = True
        self.link.write_line(json.dumps({"recording": True}))
        print(f"[macropad-bridge] voice recording started ({trigger}, backend={backend})",
              flush=True)

    def _stop_voice(self, trigger: str) -> None:
        from . import sessions, voice

        self._recording = False
        self.link.write_line(json.dumps({"recording": False}))
        recorder, self._recorder = self._recorder, None
        stt, self._stt = self._stt, None
        session_key = self._selected_key
        streaming = getattr(stt, "streaming", False) if stt else False

        def finish(text: str | None) -> None:
            if streaming:
                if text and len(text) > self._streamed_chars:
                    remainder = text[self._streamed_chars :]
                    if remainder:
                        sessions.send_to_session(session_key, remainder, send_enter=False)
                ok = sessions.send_to_session(session_key, "", send_enter=True)
            elif text:
                ok = sessions.send_to_session(session_key, text, send_enter=True)
            else:
                print("[macropad-bridge] voice capture produced no usable transcription",
                      flush=True)
                return
            status = "sent" if ok else "failed (no tmux session running for the selected key?)"
            shown = text or "(streamed)"
            print(f"[macropad-bridge] voice -> session key={session_key} {status}: "
                  f"\"{shown}\"", flush=True)

        if streaming:
            print(f"[macropad-bridge] voice recording stopped ({trigger}), finalizing stream...",
                  flush=True)
            threading.Thread(
                target=self._finish_streaming_voice,
                args=(recorder, stt, finish),
                daemon=True,
            ).start()
            return

        print(f"[macropad-bridge] voice recording stopped ({trigger}), transcribing...",
              flush=True)
        threading.Thread(
            target=self._finish_batch_voice,
            args=(recorder, stt, finish),
            daemon=True,
        ).start()

    def _finish_batch_voice(self, recorder, stt, finish) -> None:
        audio = recorder.stop() if recorder else None
        try:
            if audio is not None and stt is not None:
                stt.feed_audio(audio)
            text = stt.stop() if stt is not None else None
        finally:
            if stt is not None:
                stt.close()
        finish(text)

    def _finish_streaming_voice(self, recorder, stt, finish) -> None:
        if recorder is not None:
            recorder.stop()
        try:
            text = stt.stop() if stt is not None else None
        finally:
            if stt is not None:
                stt.close()
        finish(text)

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
        if model != self._last_sent_model:
            self._last_sent_model = model
            self.link.write_line(json.dumps({"model": model}))

    def _push_effort_to_oled(self) -> None:
        """Sends the selected session's last-known effort level (see
        _advance_effort's docstring for why this is "last set", not
        "detected") to the OLED. Independent of the model poll above so a
        cycle_effort press updates the display immediately without
        waiting for the next scheduled model poll."""
        effort = self._last_sent_effort.get(self._selected_key)
        self.link.write_line(json.dumps({"effort": effort}))

    def _push_encoder_mode(self) -> None:
        self.link.write_line(json.dumps({"encoder_mode": self._encoder_rotation_mode}))

    def _model_loop(self) -> None:
        while not self._model_stop.is_set():
            try:
                self._poll_model_once()
                # Piggyback the selected-session label, effort level, and
                # encoder mode on this same loop -- cheap, and self-heals
                # the one-shot pushes in serve_forever()/_on_board_line()
                # if they raced the serial connection still (re)connecting.
                self._send_selected_label()
                self._push_effort_to_oled()
                self._push_encoder_mode()
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
        self._push_encoder_mode()
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
    from .stt.env import load_dotenv_once

    load_dotenv_once()
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
        encoder_settings=settings.get("encoder", {}),
    )
    bridge.serve_forever()


if __name__ == "__main__":
    main()
