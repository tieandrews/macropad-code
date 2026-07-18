"""Shared STT backend protocol."""
from __future__ import annotations

from typing import Callable, Optional, Protocol

import numpy as np

DeltaCallback = Callable[[str], None]
TranscriptCallback = Callable[[str], None]


class STTBackend(Protocol):
    """Batch backends transcribe a full buffer after recording stops.
    Streaming backends open a live session on start(), accept chunks via
    feed_audio(), and finish on stop()."""

    @property
    def streaming(self) -> bool:
        ...

    def start(
        self,
        on_delta: Optional[DeltaCallback] = None,
        on_completed: Optional[TranscriptCallback] = None,
    ) -> bool:
        ...

    def feed_audio(self, audio: np.ndarray) -> None:
        ...

    def stop(self) -> Optional[str]:
        """For batch: transcribe accumulated/stopped audio and return text.
        For streaming: commit, wait briefly for final transcript, return it."""

    def close(self) -> None:
        ...


class BatchBackendMixin:
    streaming = False

    def start(
        self,
        on_delta: Optional[DeltaCallback] = None,
        on_completed: Optional[TranscriptCallback] = None,
    ) -> bool:
        return True

    def feed_audio(self, audio: np.ndarray) -> None:
        pass

    def close(self) -> None:
        pass
