"""Speech-to-text backends for macropad voice input.

See config/bridge.yaml `voice:` and docs/customization.md. Backends:

  local_whisper          offline faster-whisper (batch, after stop)
  openai_whisper         OpenAI whisper-1 (batch)
  openai_gpt4o_mini      OpenAI gpt-4o-mini-transcribe (batch, cheaper)
  openai_realtime        OpenAI gpt-realtime-whisper (streaming deltas)
  together_whisper       Together openai/whisper-large-v3 (batch)
  together_realtime      Together realtime WebSocket (streaming deltas)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .base import DeltaCallback, STTBackend, TranscriptCallback
    from .factory import create_backend

__all__ = [
    "STTBackend",
    "DeltaCallback",
    "TranscriptCallback",
    "create_backend",
]


def __getattr__(name: str):
    """Lazily imports .base/.factory on first access instead of at
    package-import time. Those modules (transitively) need the `voice`
    extra's heavy deps (numpy, openai, together, faster-whisper, ...),
    which aren't installed by default -- eagerly importing them here
    would break anything that merely imports something else from this
    package (e.g. bridge.py's `from .stt.env import load_dotenv_once`,
    needed on every startup regardless of whether voice is enabled)."""
    if name == "create_backend":
        from .factory import create_backend

        return create_backend
    if name in ("STTBackend", "DeltaCallback", "TranscriptCallback"):
        from . import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
