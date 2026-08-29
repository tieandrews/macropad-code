"""Shared WebSocket realtime transcription (OpenAI + Together)."""
from __future__ import annotations

import base64
import json
import threading
import time
from typing import Optional
from urllib.parse import urlencode

import numpy as np

from .audio import SAMPLE_RATE, pcm16_bytes
from .base import DeltaCallback, TranscriptCallback


class RealtimeWebSocketBackend:
    streaming = True

    def __init__(
        self,
        url: str,
        headers: list[str],
        session_update: dict,
        *,
        target_sample_rate: int = SAMPLE_RATE,
        commit_wait_seconds: float = 1.5,
    ) -> None:
        self._url = url
        self._headers = headers
        self._session_update = session_update
        self._target_sample_rate = target_sample_rate
        self._commit_wait_seconds = commit_wait_seconds
        self._ws = None
        self._lock = threading.Lock()
        self._recv_thread: threading.Thread | None = None
        self._stop_recv = threading.Event()
        self._on_delta: Optional[DeltaCallback] = None
        self._on_completed: Optional[TranscriptCallback] = None
        self._final_transcript = ""
        self._pending_chunks: list[bytes] = []
        # Set by close()/stop() if they're called while start()'s
        # WebSocket handshake (a real network round-trip) is still in
        # flight on another thread -- see bridge.py's _start_voice,
        # which now starts the mic recording before this connects, so a
        # very quick tap-to-toggle press/release can plausibly call
        # stop() before start() has returned. Without this, start()
        # would finish connecting *after* stop() already ran, leaving
        # an orphaned, never-closed WebSocket that nothing cleans up.
        self._closing = False

    def start(
        self,
        on_delta: Optional[DeltaCallback] = None,
        on_completed: Optional[TranscriptCallback] = None,
    ) -> bool:
        self._on_delta = on_delta
        self._on_completed = on_completed
        self._final_transcript = ""
        try:
            import websocket
        except ImportError:
            print("[macropad-stt] websocket-client not installed -- "
                  "run `uv sync --extra voice`", flush=True)
            return False

        try:
            ws = websocket.create_connection(
                self._url, header=self._headers, timeout=15
            )
        except Exception as exc:
            print(f"[macropad-stt] WebSocket connect failed: {exc}", flush=True)
            return False

        if self._closing:
            # stop()/close() ran while we were connecting -- this
            # session is already over, don't hand off a live connection
            # nobody will ever close.
            try:
                ws.close()
            except Exception:
                pass
            return False

        try:
            ws.send(json.dumps(self._session_update))
        except Exception as exc:
            print(f"[macropad-stt] session.update failed: {exc}", flush=True)
            try:
                ws.close()
            except Exception:
                pass
            return False

        with self._lock:
            self._ws = ws

        self._stop_recv.clear()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        with self._lock:
            for chunk in self._pending_chunks:
                self._append_pcm(chunk)
            self._pending_chunks = []
        return True

    def feed_audio(self, audio: np.ndarray) -> None:
        if audio is None or len(audio) == 0:
            return
        if self._target_sample_rate == 24000:
            pcm = pcm16_bytes(audio, sample_rate=24000)
        else:
            pcm = pcm16_bytes(audio, sample_rate=SAMPLE_RATE)
        with self._lock:
            if self._ws is None:
                self._pending_chunks.append(pcm)
                return
            self._append_pcm(pcm)

    def _append_pcm(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        payload = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode("ascii"),
        }
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:
            print(f"[macropad-stt] audio append failed: {exc}", flush=True)

    def _recv_loop(self) -> None:
        while not self._stop_recv.is_set() and self._ws is not None:
            try:
                self._ws.settimeout(0.5)
                raw = self._ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self._handle_event(event)

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "conversation.item.input_audio_transcription.delta":
            delta = event.get("delta") or ""
            if delta and self._on_delta:
                self._on_delta(delta)
        elif etype == "conversation.item.input_audio_transcription.completed":
            transcript = (event.get("transcript") or "").strip()
            if transcript:
                self._final_transcript = transcript
                if self._on_completed:
                    self._on_completed(transcript)
        elif etype == "error":
            print(f"[macropad-stt] realtime error: {event.get('error', event)}", flush=True)
            self.close()

    def stop(self) -> Optional[str]:
        self._closing = True
        # start()'s WebSocket handshake now runs concurrently with mic
        # recording (see bridge.py's _start_voice) so a very fast
        # tap-to-toggle press/release can call stop() before start() has
        # actually connected. Give it a brief bounded window to land --
        # any pending_chunks captured so far are real audio worth
        # keeping, not worth silently discarding just because the
        # network round-trip hadn't finished yet.
        deadline = time.time() + self._commit_wait_seconds
        while time.time() < deadline and self._ws is None and self._pending_chunks:
            time.sleep(0.05)

        with self._lock:
            if self._ws is not None:
                try:
                    self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                except Exception as exc:
                    print(f"[macropad-stt] audio commit failed: {exc}", flush=True)
        deadline = time.time() + self._commit_wait_seconds
        while time.time() < deadline and not self._final_transcript:
            time.sleep(0.05)
        self.close()
        return self._final_transcript or None

    def close(self) -> None:
        self._closing = True
        self._stop_recv.set()
        with self._lock:
            ws = self._ws
            self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        # _handle_event() (called from _recv_loop, i.e. from
        # _recv_thread itself) calls close() on an "error" event (e.g.
        # OpenAI's 60-minute realtime session cap) -- joining your own
        # thread raises "RuntimeError: cannot join current thread" and
        # kills _recv_thread with an uncaught traceback. Guard against
        # that; a thread can't usefully wait on itself anyway.
        if self._recv_thread is not None and self._recv_thread is not threading.current_thread():
            self._recv_thread.join(timeout=1)
            self._recv_thread = None


def openai_realtime_backend(settings: dict) -> RealtimeWebSocketBackend:
    openai_cfg = settings.get("openai") or {}
    api_key_env = openai_cfg.get("api_key_env", "OPENAI_API_KEY")
    from .env import api_key as get_key

    key = get_key(api_key_env)
    if not key:
        raise RuntimeError(f"{api_key_env} is not set")

    model = openai_cfg.get("realtime_model", "gpt-realtime-whisper")
    delay = openai_cfg.get("delay", "medium")
    language = openai_cfg.get("language")

    # Transcription sessions use intent=transcription; the model goes in
    # session.update (not ?model=, which selects a conversation session).
    url = f"wss://api.openai.com/v1/realtime?{urlencode({'intent': 'transcription'})}"
    headers = [f"Authorization: Bearer {key}"]

    transcription = {"model": model, "delay": delay}
    if language:
        transcription["language"] = language

    session_update = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": transcription,
                    "turn_detection": None,
                }
            },
        },
    }
    return RealtimeWebSocketBackend(
        url, headers, session_update, target_sample_rate=24000
    )


def together_realtime_backend(settings: dict) -> RealtimeWebSocketBackend:
    together_cfg = settings.get("together") or {}
    api_key_env = together_cfg.get("api_key_env", "TOGETHER_API_KEY")
    from .env import api_key as get_key

    key = get_key(api_key_env)
    if not key:
        raise RuntimeError(f"{api_key_env} is not set")

    model = together_cfg.get("model", "openai/whisper-large-v3")
    params = urlencode(
        {
            "intent": "transcription",
            "model": model,
            "input_audio_format": "pcm_s16le_16000",
        }
    )
    url = f"wss://api.together.ai/v1/realtime?{params}"
    headers = [f"Authorization: Bearer {key}"]

    session_update = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "transcription": {"model": model},
                }
            },
        },
    }
    return RealtimeWebSocketBackend(url, headers, session_update, target_sample_rate=SAMPLE_RATE)
