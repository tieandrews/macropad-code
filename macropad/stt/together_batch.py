"""Together AI batch transcription."""
from __future__ import annotations

import io
from typing import Optional

import numpy as np

from .audio import to_wav_bytes
from .base import BatchBackendMixin
from .env import api_key


class TogetherBatchBackend(BatchBackendMixin):
    def __init__(
        self,
        model: str = "openai/whisper-large-v3",
        api_key_env: str = "TOGETHER_API_KEY",
        language: str | None = "en",
    ) -> None:
        self._model = model
        self._api_key_env = api_key_env
        self._language = language
        self._buffer: list[np.ndarray] = []

    def feed_audio(self, audio: np.ndarray) -> None:
        if audio is not None and len(audio):
            self._buffer.append(audio.reshape(-1))

    def stop(self) -> Optional[str]:
        if not self._buffer:
            return None
        audio = np.concatenate(self._buffer)
        self._buffer = []
        key = api_key(self._api_key_env)
        if not key:
            print(f"[macropad-stt] {self._api_key_env} is not set -- see .env.example",
                  flush=True)
            return None
        try:
            from together import Together
        except ImportError:
            print("[macropad-stt] together package not installed -- "
                  "run `uv sync --extra voice`", flush=True)
            return None

        wav = to_wav_bytes(audio)
        client = Together(api_key=key)
        try:
            kwargs = {
                "model": self._model,
                "file": ("speech.wav", io.BytesIO(wav), "audio/wav"),
            }
            if self._language:
                kwargs["language"] = self._language
            result = client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            print(f"[macropad-stt] Together transcription failed: {exc}", flush=True)
            return None
        text = getattr(result, "text", None)
        return (text or "").strip() or None

    def close(self) -> None:
        self._buffer = []
