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

from typing import Callable, Optional

from .base import DeltaCallback, STTBackend, TranscriptCallback
from .factory import create_backend

__all__ = [
    "STTBackend",
    "DeltaCallback",
    "TranscriptCallback",
    "create_backend",
]
