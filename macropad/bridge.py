"""macropad-bridge: the long-running daemon that owns the MacroPad's serial
connection.

Hook scripts (macropad/hooks/*) never talk to the serial port directly --
they send a small JSON message to this process's local TCP socket instead.
That keeps exactly one thing responsible for the (fragile, single-owner)
USB connection, and means hooks never block an agent session waiting on
hardware.

Run with: macropad-bridge   (installed via pyproject.toml console_scripts)
or:       python -m macropad.bridge
"""
from __future__ import annotations

import json
import socketserver

from . import config
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

    def serve_forever(self) -> None:
        print(f"[macropad-bridge] listening on {self.host}:{self.port}", flush=True)
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
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
