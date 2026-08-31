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
    the selected session's pane via macropad/sessions.py (tmux or herdr,
    whichever config/bridge.yaml's `session_backend` selects).
  - `{"encoder_press": true}` when the encoder's push-button is
    pressed -- toggles voice-to-text recording on/off (macropad/voice.py).

A second background poller periodically snapshots the selected
session's pane to detect which Claude Code model it's using, and
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
            baudrate=baudrate, retry_seconds=retry_seconds, on_line=self._on_board_line,
            on_connect=self._on_serial_connect,
        )
        self._server = socketserver.ThreadingTCPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._server.on_message = self._on_message  # type: ignore[attr-defined]
        self._usage_stop = threading.Event()
        self._usage_thread: threading.Thread | None = None
        self._selected_key = 0
        # Which key the follow view was last successfully re-pointed at
        # -- see _sync_follow_if_needed().
        self._follow_synced_key: int | None = None

        self._model_poll_interval = model_poll_interval_seconds
        self._model_stop = threading.Event()
        self._model_thread: threading.Thread | None = None
        self._last_sent_model: str | None = "__unset__"  # force one send on first poll
        self._last_sent_branch: str | None = "__unset__"  # force one send on first poll

        self._voice_settings = voice_settings or {}
        self._recording = False
        self._recorder = None
        self._stt = None
        self._streamed_chars = 0
        # Voice press/release/review state machine -- see _on_voice_press
        # and _on_voice_release. "idle": nothing happening.
        # "recording_undecided": press just happened, haven't seen the
        # release yet (or it hasn't been long enough to classify).
        # "recording_toggle_armed": a quick tap started recording and
        # it's continuing hands-free; the next press stops it.
        # "pending_review": stopped without auto_enter, transcript is
        # sitting in the pane; the next press submits it (sends Enter).
        self._voice_state = "idle"
        self._voice_pending_session_key: int | None = None
        # Safety net for hands-free recording (config: voice.
        # max_recording_seconds) -- a quick tap arms hands-free
        # recording until a later tap stops it, so an errant button
        # press (or just forgetting) could otherwise leave the mic
        # recording indefinitely. This timer force-stops it after a
        # bounded duration regardless. Guarded by _voice_recording_id
        # so a timer from a *previous* recording can't stop a new one
        # that happened to start again before the old timer fired.
        self._voice_timeout_timer: threading.Timer | None = None
        self._voice_recording_id = 0
        self._model_index: dict[int, int] = {}  # session key -> cycle_model position
        self._effort_index: dict[int, int] = {}  # session key -> cycle_effort position
        self._last_sent_effort: dict[int, str] = {}  # session key -> last /effort level sent
        self._key_states: dict[int, str] = {}  # session key -> last state name sent (e.g. "waiting")
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
        self._key_states[key] = state
        self._push_key_state(key, state)

    def _sync_follow_if_needed(self) -> None:
        """Re-points the follow view at the selected session, but only
        when the selection has actually changed since the last
        successful sync -- not unconditionally on every call. This is
        piggybacked on the model-poll loop below (every
        model_poll_interval_seconds) purely as a self-heal for a sync
        that raced something at startup and silently failed; calling it
        unconditionally there used to mean the follow view got yanked
        back to the macropad's selection every few seconds even if you
        never touched the pad, since sessions.sync_follow_session()
        forces focus back to a specific session regardless of where a
        session_backend's own UI had navigated to in the meantime -- an
        annoyance under tmux too, but much more noticeable with herdr's
        clickable sidebar. Leaves self._follow_synced_key unset (so the
        next call retries) if the sync itself fails."""
        from . import sessions

        if self._selected_key == self._follow_synced_key:
            return
        if sessions.sync_follow_session(self._selected_key):
            self._follow_synced_key = self._selected_key

    def _push_key_state(self, key: int, state: str) -> bool:
        """Resolves `state` to a color/pulse via the currently-loaded
        colors.yaml and pushes it for `key`. Split out from _on_message
        so _reload_config can replay every already-lit key's last known
        state against newly-edited colors -- otherwise a key lit before
        an "Apply to pad" wouldn't visually update until its next hook
        event, which could be arbitrarily far off. Returns whether the
        write actually reached the board (False if it's not connected
        yet/still) -- callers that need the push to eventually land
        (see _resync_session_leds) use this to know whether to retry."""
        color = config.color_for_state(state)
        pulse = config.pulse_for_state(state)
        payload = json.dumps({"key": key, "color": color, "pulse": pulse})
        ok = self.link.write_line(payload)
        status = "sent" if ok else "dropped (MacroPad not connected)"
        print(f"[macropad-bridge] key={key} state={state} color={color} "
              f"pulse={pulse} -> {status}", flush=True)
        return ok

    def _push_action_key_colors(self) -> None:
        """Pushes each action key's (6-11) own static LED color, from
        keymap.yaml's `actions:` entries -- e.g. a subtle green Approve
        key, red Deny, etc. Unlike session keys (0-5), action keys have
        no dynamic status of their own, so there's nothing else that
        would ever push a color for them; this has to be called
        explicitly (startup, config reload, and the `type: resync`
        action) rather than happening as a side effect of some other
        event. A key with no `color` configured is left untouched (it
        just stays off, as it always has). A `type: voice_toggle` key's
        LED is entirely host/firmware-driven (see _push_mic_led) and
        ignores whatever static color is pushed here -- see firmware/
        code.py's _render_key_leds -- so it's harmless to still push one
        if it has a `color` set, but there's no need to bother."""
        actions = config.load_keymap().get("actions") or {}
        pushed = 0
        for key_str, entry in actions.items():
            entry = entry or {}
            if entry.get("type") == "voice_toggle":
                continue
            color = config.action_key_color(entry)
            if color is None:
                continue
            key = int(key_str)
            payload = json.dumps({"key": key, "color": color, "pulse": False})
            if self.link.write_line(payload):
                pushed += 1
        if pushed:
            print(f"[macropad-bridge] pushed {pushed} action key LED color(s)", flush=True)

    def _reload_config(self) -> None:
        """Re-reads config/bridge.yaml's `voice`/`encoder` settings into
        the running bridge -- everything else (colors.yaml, keymap.yaml)
        is already read fresh on every access (see macropad/config.py),
        so this only matters for the two things cached at construction
        time. Triggered by the web UI's "Apply to pad" (see webui.py) so
        changes take effect without a manual bridge restart.

        Also replays every key's last known state (self._key_states)
        against the freshly-loaded colors.yaml, so keys already lit
        before the edit visually update immediately -- otherwise a color
        change wouldn't show up on an already-lit key until its next
        unrelated hook event, which could be arbitrarily far off. Action
        keys (6-11) get the same treatment via _push_action_key_colors --
        nothing else would ever re-push their static color after an
        edit."""
        settings = config.load_bridge_settings()
        self._voice_settings = settings.get("voice", {})
        encoder_settings = settings.get("encoder", {}) or {}
        rotation_mode = encoder_settings.get("rotation_mode", "session")
        self._encoder_rotation_mode = rotation_mode if rotation_mode in ("session", "effort") else "session"
        self._push_encoder_mode()
        self._push_voice_key()  # keymap.yaml's voice_toggle key may have changed too
        for key, state in self._key_states.items():
            self._push_key_state(key, state)
        self._push_action_key_colors()
        print(f"[macropad-bridge] config reloaded (voice + encoder settings, "
              f"{len(self._key_states)} key LED(s) refreshed)", flush=True)

    def _on_serial_connect(self) -> None:
        """Called by ReconnectingSerial every time the serial link comes
        up -- including a bridge restart with the physical board already
        sitting on some other key. self._selected_key otherwise starts
        every bridge process at 0 with no way to learn better, since the
        board only sends `{"selected": N}` on an actual rotation/press,
        never proactively -- silently routing action keys/voice input
        into the wrong session until you happened to touch the pad. Asks
        the board to re-announce its current selection instead of
        guessing; firmware/code.py answers with {"selected": N} on the
        very next line, handled the same as a real rotation by
        _on_board_line() below."""
        self.link.write_line(json.dumps({"request_selected": True}))

    def _on_board_line(self, line: str) -> None:
        """Handles a message the *board* sent us: encoder selection
        changes and action-key presses -- see firmware/code.py."""
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
            self._poll_branch_once()
            self._push_effort_to_oled()
            self._sync_follow_if_needed()
            return

        if "action" in msg:
            try:
                action_key = int(msg["action"])
            except (TypeError, ValueError):
                return
            self._dispatch_action(action_key)
            return

        if "voice_press" in msg:
            source = msg.get("source", "?")
            if source == "encoder":
                triggers = self._voice_settings.get("triggers") or {}
                if not triggers.get("encoder", True):
                    return
            self._on_voice_press(trigger=f"{source} press")
            return

        if "voice_release" in msg:
            try:
                held_ms = int(msg.get("held_ms", 0))
            except (TypeError, ValueError):
                held_ms = 0
            source = msg.get("source", "?")
            self._on_voice_release(held_ms, trigger=f"{source} release")
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
            # Normally unreachable: current firmware diverts this key's
            # presses into {"voice_press"/"voice_release"} instead of
            # {"action": N} once it's been announced via _push_voice_key()
            # (see firmware/code.py's _poll_keys). Kept as a fallback for
            # older/mismatched firmware that doesn't know about that yet.
            self._on_voice_press(trigger=f"action key {action_key} ({label})")
            return

        if entry.get("type") == "resync":
            self._resync_session_leds(force=True)
            self._push_action_key_colors()
            print(f"[macropad-bridge] action='{label}' -> resynced all session key LEDs",
                  flush=True)
            return

        from . import sessions

        ok = sessions.send_to_session(
            self._selected_key, entry.get("send_keys", ""), entry.get("enter", True)
        )
        status = "sent" if ok else "failed (no session running for the selected key?)"
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
        status = "sent" if ok else "failed (no session running for the selected key?)"
        print(f"[macropad-bridge] action='{label}' -> session key={key} switching to "
              f"'{next_model}' {status}", flush=True)
        if ok:
            self._poll_model_once()  # refresh the OLED once the switch has had a moment to land
            # Claude Code sometimes (not always) shows a "Switch model?"
            # confirmation menu instead of switching right away -- see
            # sessions.confirm_model_switch_if_pending's docstring. Runs
            # in the background so a pending confirmation's ~0.5-2s poll
            # never delays the next key press being handled.
            threading.Thread(
                target=self._confirm_model_switch, args=(key, next_model), daemon=True
            ).start()

    def _confirm_model_switch(self, key: int, next_model: str) -> None:
        from . import sessions

        if sessions.confirm_model_switch_if_pending(key):
            print(f"[macropad-bridge] session key={key} confirmed pending "
                  f"'Switch model?' prompt for '{next_model}'", flush=True)
            self._poll_model_once()  # the switch only just actually landed -- refresh the OLED again

    def _advance_effort(self, key: int, levels: list, step: int, trigger: str) -> None:
        """Shared by the cycle_effort action key and encoder rotation (in
        effort mode) -- advances `key`'s effort-level position by `step`
        and sends `/effort <level>` into its session. There's no way to
        detect Claude Code's *actual* current effort level from the
        session's pane (unlike model name -- see sessions.detect_model()), so
        self._last_sent_effort only reflects what this bridge itself has
        sent since it started, not ground truth."""
        from . import sessions

        if not levels:
            return
        index = (self._effort_index.get(key, -1) + step) % len(levels)
        self._effort_index[key] = index
        next_level = levels[index]

        ok = sessions.send_to_session(key, f"/effort {next_level}", True)
        status = "sent" if ok else "failed (no session running for the selected key?)"
        print(f"[macropad-bridge] {trigger} -> session key={key} switching effort to "
              f"'{next_level}' {status}", flush=True)
        if ok:
            self._last_sent_effort[key] = next_level
            if key == self._selected_key:
                self._push_effort_to_oled()

    # Below this held_ms, a press/release is a "tap" (arms toggle mode --
    # recording keeps going hands-free until the next press). At or above
    # it, the release itself stops recording (hold-to-talk). See
    # config/bridge.yaml's voice.hold_threshold_ms.
    DEFAULT_HOLD_THRESHOLD_MS = 300

    # Fallback if voice.max_recording_seconds isn't set in bridge.yaml
    # at all (existing configs from before this existed) -- still gets
    # a safety net rather than none. 0/null in config disables it.
    DEFAULT_MAX_RECORDING_SECONDS = 600

    def _on_voice_press(self, trigger: str) -> None:
        if not self._voice_settings.get("enabled", False):
            print("[macropad-bridge] voice press but voice input is disabled -- set "
                  "voice.enabled: true in config/bridge.yaml. See docs/customization.md.",
                  flush=True)
            return

        if self._voice_state == "idle":
            if self._start_voice(trigger):
                self._voice_state = "recording_undecided"
            return

        if self._voice_state == "recording_toggle_armed":
            # Second tap while hands-free recording -- this press means
            # "stop", same as the old tap-to-toggle behavior.
            self._stop_voice(trigger)
            return

        if self._voice_state == "pending_review":
            # Transcript is sitting in the pane awaiting confirmation --
            # this press submits it (sends Enter) rather than starting a
            # new recording.
            self._submit_pending_review(trigger)
            return

        # "recording_undecided": a press here would mean the button is
        # somehow reporting a second press-down before its release --
        # shouldn't happen physically, ignore defensively.

    def _on_voice_release(self, held_ms: int, trigger: str) -> None:
        if self._voice_state != "recording_undecided":
            # Release from a press that was actually the "stop" tap (in
            # recording_toggle_armed) or the "confirm" tap (in
            # pending_review) -- already fully handled on press, nothing
            # more to do on release.
            return

        threshold = (self._voice_settings.get("hold_threshold_ms")
                     or self.DEFAULT_HOLD_THRESHOLD_MS)
        if held_ms >= threshold:
            self._stop_voice(f"{trigger}, held {held_ms}ms")
        else:
            self._voice_state = "recording_toggle_armed"
            print(f"[macropad-bridge] voice recording continuing hands-free "
                  f"({trigger}, held {held_ms}ms < {threshold}ms threshold -- tap again to stop)",
                  flush=True)

    def _submit_pending_review(self, trigger: str) -> None:
        from . import sessions

        key = self._voice_pending_session_key
        self._voice_pending_session_key = None
        self._voice_state = "idle"
        self._push_mic_led()
        if key is None:
            return
        ok = sessions.send_to_session(key, "", send_enter=True)
        status = "sent" if ok else "failed (no session running for the selected key?)"
        print(f"[macropad-bridge] voice transcript confirmed ({trigger}) -> session key={key} "
              f"{status}", flush=True)

    def _start_voice(self, trigger: str) -> bool:
        from . import voice

        try:
            from .stt import create_backend

            self._stt = create_backend(self._voice_settings)
        except Exception as exc:
            hint = (" -- run `uv sync --extra voice` to install its dependencies"
                    if isinstance(exc, ImportError) else "")
            print(f"[macropad-bridge] voice backend setup failed: {exc}{hint}", flush=True)
            self._stt = None
            return False

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

            # Start the mic capturing *before* the streaming backend's
            # handshake (a real network round-trip -- e.g. a WebSocket
            # connect to OpenAI/Together, easily 100ms-1s+) rather than
            # after it. feed_audio() on these backends already buffers
            # audio it receives before the connection is up (see
            # RealtimeWebSocketBackend._pending_chunks), so nothing is
            # lost -- this just means the mic LED/recording state (and
            # the mic itself) go live the instant you press, instead of
            # waiting on the network before you get any feedback at all.
            recorder = voice.Recorder()
            if not recorder.start(on_chunk=self._stt.feed_audio):
                print("[macropad-bridge] couldn't start voice recording (no microphone / "
                      "missing dependencies -- see docs/customization.md)", flush=True)
                self._stt.close()
                self._stt = None
                return False

            stt = self._stt

            def connect_backend() -> None:
                if not stt.start(on_delta=on_delta, on_completed=on_completed):
                    print(f"[macropad-bridge] couldn't start streaming STT ({backend})",
                          flush=True)

            threading.Thread(target=connect_backend, daemon=True).start()
        else:
            recorder = voice.Recorder()
            if not recorder.start():
                print("[macropad-bridge] couldn't start voice recording (no microphone / "
                      "missing dependencies -- see docs/customization.md)", flush=True)
                self._stt.close()
                self._stt = None
                return False

        self._recorder = recorder
        self._recording = True
        self._voice_pending_session_key = self._selected_key
        self._voice_recording_id += 1
        self._arm_voice_timeout(self._voice_recording_id)
        self._push_mic_led()
        print(f"[macropad-bridge] voice recording started ({trigger}, backend={backend})",
              flush=True)
        return True

    def _arm_voice_timeout(self, recording_id: int) -> None:
        max_seconds = self._voice_settings.get("max_recording_seconds",
                                                 self.DEFAULT_MAX_RECORDING_SECONDS)
        if not max_seconds or max_seconds <= 0:
            return
        timer = threading.Timer(max_seconds, self._on_voice_timeout, args=(recording_id,))
        timer.daemon = True
        self._voice_timeout_timer = timer
        timer.start()

    def _cancel_voice_timeout(self) -> None:
        timer, self._voice_timeout_timer = self._voice_timeout_timer, None
        if timer is not None:
            timer.cancel()

    def _on_voice_timeout(self, recording_id: int) -> None:
        # The timer that just fired belonged to whichever recording was
        # active `max_recording_seconds` ago -- if a stop+restart
        # happened in the meantime, recording_id has since moved on and
        # this is stale; ignore it rather than stopping the new one.
        if not self._recording or recording_id != self._voice_recording_id:
            return
        max_seconds = self._voice_settings.get("max_recording_seconds",
                                                 self.DEFAULT_MAX_RECORDING_SECONDS)
        print(f"[macropad-bridge] voice recording hit max duration "
              f"({max_seconds}s) -- auto-stopping", flush=True)
        self._stop_voice(f"max duration {max_seconds}s reached")

    def _stop_voice(self, trigger: str) -> None:
        from . import sessions, voice

        self._cancel_voice_timeout()
        self._recording = False
        recorder, self._recorder = self._recorder, None
        stt, self._stt = self._stt, None
        session_key = self._selected_key
        streaming = getattr(stt, "streaming", False) if stt else False
        auto_enter = self._voice_settings.get("auto_enter", True)

        def finish(text: str | None) -> None:
            if streaming:
                if text and len(text) > self._streamed_chars:
                    remainder = text[self._streamed_chars :]
                    if remainder:
                        sessions.send_to_session(session_key, remainder, send_enter=False)
                got_text = True
            elif text:
                sessions.send_to_session(session_key, text, send_enter=False)
                got_text = True
            else:
                print("[macropad-bridge] voice capture produced no usable transcription",
                      flush=True)
                self._voice_state = "idle"
                self._push_mic_led()
                return

            if auto_enter:
                ok = sessions.send_to_session(session_key, "", send_enter=True)
                self._voice_state = "idle"
            else:
                ok = got_text
                self._voice_pending_session_key = session_key
                self._voice_state = "pending_review"
            self._push_mic_led()
            status = "sent" if ok else "failed (no session running for the selected key?)"
            shown = text or "(streamed)"
            suffix = "" if auto_enter else " (pending review -- tap mic again to submit)"
            print(f"[macropad-bridge] voice -> session key={session_key} {status}: "
                  f"\"{shown}\"{suffix}", flush=True)

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

    def _poll_branch_once(self) -> None:
        from . import sessions

        branch = sessions.detect_branch(self._selected_key)
        if branch != self._last_sent_branch:
            self._last_sent_branch = branch
            self.link.write_line(json.dumps({"branch": branch}))

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

    def _voice_key(self) -> int | None:
        """Which action key (if any) has `type: voice_toggle` in
        config/keymap.yaml -- the firmware needs to know this so it can
        track press/release timing for that key instead of firing the
        normal one-shot {"action": N} on press (see firmware/code.py's
        _poll_keys). Only the first match is used if more than one key
        is (unusually) configured this way."""
        actions = config.load_keymap().get("actions") or {}
        for key, entry in actions.items():
            if (entry or {}).get("type") == "voice_toggle":
                try:
                    return int(key)
                except (TypeError, ValueError):
                    continue
        return None

    def _push_voice_key(self) -> None:
        self.link.write_line(json.dumps({"voice_key": self._voice_key()}))

    def _push_mic_led(self) -> None:
        led_state = {
            "idle": "idle",
            "recording_undecided": "recording",
            "recording_toggle_armed": "recording",
            "pending_review": "pending_review",
        }.get(self._voice_state, "idle")
        self.link.write_line(json.dumps({"mic_led": led_state}))

    def _model_loop(self) -> None:
        while not self._model_stop.is_set():
            try:
                self._poll_model_once()
                self._poll_branch_once()
                # Piggyback the selected-session label, effort level,
                # encoder mode, and mic key/LED on this same loop --
                # cheap, and self-heals the one-shot pushes in
                # serve_forever()/_on_board_line() if they raced the
                # serial connection still (re)connecting.
                self._send_selected_label()
                self._push_effort_to_oled()
                self._push_encoder_mode()
                self._push_voice_key()
                self._push_mic_led()
                self._resync_session_leds()  # catches any key dropped by a startup serial race
                self._sync_follow_if_needed()
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

    def _resync_session_leds(self, force: bool = False) -> None:
        """Makes every session key's (0-5) LED match reality: `waiting`
        for a key with a live session, `idle` for a key that's
        unconfigured or whose session isn't running. This matters
        because the MacroPad's NeoPixels have no memory of their own on
        the *host* side -- they hold whatever color they were last told
        until the board itself reboots (a serial reconnect / bridge
        restart does NOT reset them, only the physical firmware does).
        Without this, a key can be stuck showing a stale color from
        hours/days ago (e.g. leftover test traffic, or a session that
        was since removed from keymap.yaml) with nothing to ever clear
        it -- see docs/customization.md's "Resyncing LEDs" section.

        Called on bridge startup (and self-healed via the model-poll
        loop for anything a startup serial race dropped) with
        force=False, which only touches keys not yet tracked in
        self._key_states, so it never regresses a real hook-reported
        state back to a generic `waiting`/`idle`. Called with force=True
        by the `type: resync` action key (see _dispatch_action) for an
        on-demand full resync -- that path deliberately re-pushes every
        key regardless of tracked state, since the whole point of a
        manual resync button is "make it match reality right now",
        overriding anything stale."""
        from . import sessions

        keys = config.load_keymap().get("keys") or {}
        for key in range(sessions.SESSION_KEY_COUNT):
            if not force and key in self._key_states:
                continue
            entry = keys.get(key) or keys.get(str(key))
            has_project = bool((entry or {}).get("project_path"))
            state = "waiting" if (has_project and sessions.has_session(key)) else "idle"
            # Only record it once the write actually lands -- if the
            # serial link isn't connected yet (a startup race), leave it
            # untracked (force=False path) so the next model-poll cycle
            # retries this same key instead of silently giving up on it.
            if self._push_key_state(key, state) or force:
                self._key_states[key] = state

    def serve_forever(self) -> None:
        print(f"[macropad-bridge] listening on {self.host}:{self.port}", flush=True)
        self._send_selected_label()  # push key 0's label immediately, don't wait for a rotation
        self._poll_branch_once()
        self._push_encoder_mode()
        self._push_voice_key()
        self._push_mic_led()
        self._resync_session_leds()
        self._push_action_key_colors()
        self._sync_follow_if_needed()
        if self._voice_settings.get("enabled", False):
            # Pays PortAudio's one-time cold-start cost now instead of on
            # the user's first mic press -- see voice.warm_up(). Off the
            # main thread since it can block briefly on slow hardware.
            from . import voice

            threading.Thread(target=voice.warm_up, daemon=True).start()
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
