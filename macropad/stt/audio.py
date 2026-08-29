"""Audio format helpers shared by STT backends."""
from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16000


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    clipped = np.clip(audio.reshape(-1), -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def resample_linear(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Simple linear resample -- good enough for speech STT."""
    if from_rate == to_rate:
        return audio.reshape(-1)
    source = audio.reshape(-1).astype(np.float32)
    if len(source) == 0:
        return source
    duration = len(source) / from_rate
    target_len = max(1, int(round(duration * to_rate)))
    x_old = np.linspace(0.0, 1.0, num=len(source), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, source).astype(np.float32)


def pcm16_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    if sample_rate != SAMPLE_RATE:
        audio = resample_linear(audio, SAMPLE_RATE, sample_rate)
    return float32_to_pcm16(audio)


def to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    pcm = float32_to_pcm16(audio)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
