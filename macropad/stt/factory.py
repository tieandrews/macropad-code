"""STT backend factory -- reads config/bridge.yaml voice section."""
from __future__ import annotations

from typing import Any

from .env import load_dotenv_once
from .local import LocalWhisperBackend
from .openai_batch import OpenAIBatchBackend
from .realtime_ws import openai_realtime_backend, together_realtime_backend
from .together_batch import TogetherBatchBackend

BACKENDS = {
    "local_whisper",
    "openai_whisper",
    "openai_gpt4o_mini",
    "openai_realtime",
    "together_whisper",
    "together_realtime",
}


def create_backend(voice_settings: dict) -> Any:
    load_dotenv_once()
    backend = voice_settings.get("backend", "local_whisper")
    openai_cfg = voice_settings.get("openai") or {}
    together_cfg = voice_settings.get("together") or {}

    if backend == "local_whisper":
        return LocalWhisperBackend(voice_settings.get("whisper_model", "base"))

    if backend == "openai_whisper":
        return OpenAIBatchBackend(
            model=openai_cfg.get("model", "whisper-1"),
            api_key_env=openai_cfg.get("api_key_env", "OPENAI_API_KEY"),
            language=openai_cfg.get("language"),
        )

    if backend == "openai_gpt4o_mini":
        return OpenAIBatchBackend(
            model=openai_cfg.get("model", "gpt-4o-mini-transcribe"),
            api_key_env=openai_cfg.get("api_key_env", "OPENAI_API_KEY"),
            language=openai_cfg.get("language"),
        )

    if backend == "openai_realtime":
        return openai_realtime_backend(voice_settings)

    if backend == "together_whisper":
        return TogetherBatchBackend(
            model=together_cfg.get("model", "openai/whisper-large-v3"),
            api_key_env=together_cfg.get("api_key_env", "TOGETHER_API_KEY"),
            language=together_cfg.get("language", "en"),
        )

    if backend == "together_realtime":
        return together_realtime_backend(voice_settings)

    raise ValueError(
        f"Unknown voice.backend '{backend}' -- expected one of: {', '.join(sorted(BACKENDS))}"
    )
