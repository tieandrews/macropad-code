"""OpenAI Audio API batch transcription (whisper-1, gpt-4o-mini-transcribe, ...)."""
from __future__ import annotations

import io
from typing import Optional

import numpy as np

from .audio import to_wav_bytes
from .base import BatchBackendMixin
from .env import api_key


class OpenAIBatchBackend(BatchBackendMixin):
    def __init__(
        self,
        model: str = "whisper-1",
        api_key_env: str = "OPENAI_API_KEY",
        language: str | None = None,
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
            from openai import OpenAI
        except ImportError:
            print("[macropad-stt] openai package not installed -- "
                  "run `uv sync --extra voice`", flush=True)
            return None

        wav = to_wav_bytes(audio)
        client = OpenAI(api_key=key)
        try:
            kwargs = {"model": self._model, "file": ("speech.wav", io.BytesIO(wav), "audio/wav")}
            if self._language:
                kwargs["language"] = self._language
            result = client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            print(f"[macropad-stt] OpenAI transcription failed: {exc}", flush=True)
            return None
        text = getattr(result, "text", None) or (result.get("text") if isinstance(result, dict) else None)
        return (text or "").strip() or None

    def close(self) -> None:
        self._buffer = []
