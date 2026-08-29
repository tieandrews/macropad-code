"""Local faster-whisper batch transcription."""
from __future__ import annotations

from typing import Optional

import numpy as np

from .base import BatchBackendMixin, DeltaCallback, TranscriptCallback


class LocalWhisperBackend(BatchBackendMixin):
    def __init__(self, model_size: str = "base") -> None:
        self._model_size = model_size
        self._buffer: list[np.ndarray] = []

    def feed_audio(self, audio: np.ndarray) -> None:
        if audio is not None and len(audio):
            self._buffer.append(audio.reshape(-1))

    def stop(self) -> Optional[str]:
        if not self._buffer:
            return None
        import numpy as np

        audio = np.concatenate(self._buffer)
        self._buffer = []
        from macropad import voice

        return voice.transcribe(audio, self._model_size)

    def close(self) -> None:
        self._buffer = []
