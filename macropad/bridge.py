"""macropad-bridge: the long-running daemon that owns the MacroPad's serial
connection.

Hook scripts (macropad/hooks/*) never talk to the serial port directly --
they send a small JSON message to this process's local TCP socket instead.
That keeps exactly one thing responsible for the (fragile, single-owner)
USB connection, and means hooks never block an agent session waiting on
hardware.

This process also owns the optional usage-display poller: a background
thread that periodically reads local Claude Code usage (see usage.py) and
pushes it straight to the MacroPad's OLED over the same serial link --
independent of the per-key LED status traffic coming from hooks.

Run with: macropad-bridge   (installed via pyproject.toml console_scripts)
or:       python -m macropad.bridge
"""
from __future__ import annotations

import json
import socketserver
import threading

from . import config, usage
from .serial_link import ReconnectingSerial

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        self.server.on_message(msg)  # type: ignore[attr-defined]


class Bridge:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 baudrate: int = 115200, retry_seconds: float = 2.0):
        self.host = host
        self.port = port
        self.link = ReconnectingSerial(baudrate=baudrate, retry_seconds=retry_seconds)
        self._server = socketserver.ThreadingTCPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._server.on_message = self._on_message  # type: ignore[attr-defined]
        self._usage_stop = threading.Event()
        self._usage_thread: threading.Thread | None = None

    def _on_message(self, msg: dict) -> None:
        key = msg.get("key")
        state = msg.get("state")
        if key is None or state is None:
            return
        color = config.color_for_state(state)
        payload = json.dumps({"key": key, "color": color})
        ok = self.link.write_line(payload)
        status = "sent" if ok else "dropped (MacroPad not connected)"
        print(f"[macropad-bridge] key={key} state={state} color={color} -> {status}", flush=True)

    def _usage_loop(self, settings: dict) -> None:
        interval = settings.get("poll_interval_seconds", 60)
        while not self._usage_stop.is_set():
            try:
                payload = usage.build_display_payload(settings)
                ok = self.link.write_line(json.dumps(payload))
                if ok:
                    s = payload["usage"]["session"]["value"]
                    w = payload["usage"]["weekly"]["value"]
                    print(f"[macropad-bridge] usage session={s} weekly={w} -> sent", flush=True)
            except Exception as exc:  # best-effort: never let usage polling kill the bridge
                print(f"[macropad-bridge] usage poll failed: {exc}", flush=True)
            self._usage_stop.wait(interval)

    def start_usage_poller(self) -> None:
        settings = config.load_usage_settings()
        if not settings.get("enabled", False):
            return

        source = settings.get("source", "local_estimate")
        interval = settings.get("poll_interval_seconds", 60)
        if source == "claude_pty":
            if interval < 300:
                print(f"[macropad-bridge] warning: usage source is claude_pty but "
                      f"poll_interval_seconds is {interval}s -- this launches a full "
                      "claude process every poll; consider 300s+. See "
                      "config/usage.yaml / docs/customization.md.", flush=True)
            try:
                import pyte  # noqa: F401
            except ImportError:
                print("[macropad-bridge] warning: usage source is claude_pty but the "
                      "optional 'pyte' package isn't installed (uv sync --extra pty) "
                      "-- falling back to a cruder ANSI stripper.", flush=True)

        self._usage_thread = threading.Thread(
            target=self._usage_loop, args=(settings,), daemon=True
        )
        self._usage_thread.start()
        print(f"[macropad-bridge] usage display enabled (source={source}, "
              f"every {interval}s)", flush=True)

    def serve_forever(self) -> None:
        print(f"[macropad-bridge] listening on {self.host}:{self.port}", flush=True)
        self.start_usage_poller()
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._usage_stop.set()
            self._server.server_close()
            self.link.close()


def main() -> None:
    settings = config.load_bridge_settings()
    serial_settings = settings.get("serial", {})
    bridge = Bridge(
        host=settings.get("host", DEFAULT_HOST),
        port=settings.get("port", DEFAULT_PORT),
        baudrate=serial_settings.get("baudrate", 115200),
        retry_seconds=serial_settings.get("retry_seconds", 2.0),
    )
    bridge.serve_forever()


if __name__ == "__main__":
    main()
