"""Microphone capture for macropad voice input.

Recording uses PortAudio's blocking API (see module docstring in the
original design). Transcription itself lives in macropad/stt/.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

SAMPLE_RATE = 16000
_CHUNK_FRAMES = 1024

ChunkCallback = Callable[[object], None]


class Recorder:
    """Accumulates microphone frames between `start()` and `stop()`.
    Optionally calls `on_chunk` with each float32 mono frame for live
    streaming STT backends."""

    def __init__(self) -> None:
        self._stream = None
        self._frames: list = []
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._on_chunk: Optional[ChunkCallback] = None

    def start(self, on_chunk: Optional[ChunkCallback] = None) -> bool:
        self._on_chunk = on_chunk
        try:
            import sounddevice as sd
        except ImportError:
            print("[macropad-voice] the optional 'sounddevice' package isn't installed "
                  "-- run `uv sync --extra voice` to enable voice input.", flush=True)
            return False
        except OSError as exc:
            print(f"[macropad-voice] sounddevice can't find the PortAudio library ({exc}) "
                  "-- install it: `sudo apt install libportaudio2` (Debian/Ubuntu/WSL).",
                  flush=True)
            return False

        self._frames = []
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
            self._stream.start()
        except Exception as exc:
            print(f"[macropad-voice] failed to open microphone: {exc}", flush=True)
            self._stream = None
            return False

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk, _overflowed = self._stream.read(_CHUNK_FRAMES)
            except Exception as exc:
                print(f"[macropad-voice] microphone read failed: {exc}", flush=True)
                return
            flat = chunk.copy().reshape(-1)
            self._frames.append(flat)
            if self._on_chunk is not None:
                try:
                    self._on_chunk(flat)
                except Exception as exc:
                    print(f"[macropad-voice] on_chunk callback failed: {exc}", flush=True)

    def stop(self):
        if self._stream is None:
            return None
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._thread = None
        self._on_chunk = None

        if not self._frames:
            return None
        import numpy as np

        audio = np.concatenate(self._frames, axis=0).reshape(-1)
        self._frames = []
        return audio


_model = None


def transcribe(audio, model_size: str = "base") -> Optional[str]:
    """Legacy local faster-whisper helper (used by stt/local.py)."""
    if audio is None or len(audio) == 0:
        return None
    global _model
    try:
        if _model is None:
            from faster_whisper import WhisperModel

            print(f"[macropad-voice] loading local Whisper model '{model_size}' "
                  "(first use only)...", flush=True)
            _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = _model.transcribe(audio, language=None)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except ImportError:
        print("[macropad-voice] faster-whisper not installed -- "
              "run `uv sync --extra voice`", flush=True)
        return None
    except Exception as exc:
        print(f"[macropad-voice] transcription failed: {exc}", flush=True)
        return None
    return text or None
