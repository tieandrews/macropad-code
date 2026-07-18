"""Speech-to-text for the encoder-press "voice input" feature.

Pressing the MacroPad's rotary encoder toggles this: press once to start
recording from the host machine's default microphone, press again to
stop, transcribe locally, and send the result into the currently
selected session's tmux pane -- see macropad/bridge.py's
`_on_encoder_press`.

Design notes:

- The MacroPad itself has no microphone. Recording happens on whatever
  machine runs `macropad-bridge`, using its default input device
  (`sounddevice`, which wraps PortAudio). If that machine is a WSL
  instance, this depends on WSLg's PulseAudio bridge already forwarding
  your Windows microphone in -- check with
  `python -c "import sounddevice; print(sounddevice.query_devices())"`.
  If PortAudio can't find a usable input device, `record_toggle()` below
  fails cleanly (returns None) rather than hanging or crashing the
  bridge.
- Transcription is local and offline: `faster-whisper`, a CPU-friendly
  reimplementation of OpenAI's Whisper. The model is downloaded once (a
  few hundred MB for the default "base" size) and cached by
  huggingface_hub/faster-whisper under `~/.cache`, then kept loaded in
  memory for the life of the bridge process -- the first transcription
  after a bridge (re)start is slower while it loads.
- Requires the optional `voice` extra: `uv sync --extra voice`
  (`sounddevice`, `numpy`, `faster-whisper`). Everything in this module
  degrades to returning None/False rather than raising if that extra
  isn't installed, consistent with every other optional source in this
  repo (usage_monitor.py, usage_pty.py).
"""
from __future__ import annotations

import threading
from typing import Optional

SAMPLE_RATE = 16000  # what Whisper's feature extractor expects
_CHUNK_FRAMES = 1024


class Recorder:
    """Accumulates microphone frames between `start()` and `stop()`.
    Never raises: if the optional `sounddevice` dependency is missing or
    no input device is available, `start()` returns False and `stop()`
    returns None, and the bridge treats that like any other "nothing to
    do" case.

    Deliberately uses PortAudio's *blocking* API (a plain `.read()` loop
    on our own thread) rather than its callback API (`InputStream(...,
    callback=...)`). The callback API spawns a dedicated real-time
    PortAudio thread that has a known race-condition bug
    (https://github.com/PortAudio/portaudio/issues/1011) triggering
    `paTimedOut` fairly reliably under WSL/virtualized environments --
    the blocking API avoids that code path entirely."""

    def __init__(self) -> None:
        self._stream = None
        self._frames: list = []
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        try:
            import sounddevice as sd
        except ImportError:
            print("[macropad-voice] the optional 'sounddevice' package isn't installed "
                  "-- run `uv sync --extra voice` to enable voice input.", flush=True)
            return False
        except OSError as exc:
            # sounddevice raises this at import time (not ImportError) when
            # the system-level PortAudio library itself is missing.
            print(f"[macropad-voice] sounddevice can't find the PortAudio library ({exc}) "
                  "-- install it: `sudo apt install libportaudio2` (Debian/Ubuntu/WSL).",
                  flush=True)
            return False

        self._frames = []
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
            self._stream.start()
        except Exception as exc:  # sounddevice raises its own PortAudioError subclasses
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
            self._frames.append(chunk.copy())

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

        if not self._frames:
            return None
        import numpy as np

        audio = np.concatenate(self._frames, axis=0).reshape(-1)
        self._frames = []
        return audio


_model = None  # lazily loaded, cached for the life of the process


def _get_model(model_size: str):
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel

    print(f"[macropad-voice] loading local Whisper model '{model_size}' "
          "(first use only -- this can take a while, longer still if it "
          "needs to download)...", flush=True)
    _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model


def transcribe(audio, model_size: str = "base") -> Optional[str]:
    """Best-effort local transcription. Returns None (never raises) if
    the optional `faster-whisper`/`numpy` dependencies are missing, the
    model fails to load, or `audio` is empty."""
    if audio is None or len(audio) == 0:
        return None
    try:
        model = _get_model(model_size)
    except ImportError:
        print("[macropad-voice] the optional 'faster-whisper' package isn't installed "
              "-- run `uv sync --extra voice` to enable voice input.", flush=True)
        return None
    except Exception as exc:
        print(f"[macropad-voice] failed to load Whisper model: {exc}", flush=True)
        return None

    try:
        segments, _info = model.transcribe(audio, language=None)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        print(f"[macropad-voice] transcription failed: {exc}", flush=True)
        return None
    return text or None
