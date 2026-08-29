"""Tiny client used by hook scripts to notify the running macropad-bridge
daemon of a state change. Deliberately dumb and fast-failing: a hook must
never block or error out an agent session just because the bridge isn't
running or the MacroPad isn't plugged in.
"""
from __future__ import annotations

import json
import socket

from . import config

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999
TIMEOUT_SECONDS = 0.5


def send_state(key: int, state: str) -> bool:
    """Best-effort notification. Returns False (never raises) on any
    failure -- daemon not running, socket refused, timeout, etc."""
    settings = config.load_bridge_settings()
    host = settings.get("host", DEFAULT_HOST)
    port = settings.get("port", DEFAULT_PORT)
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as sock:
            sock.sendall((json.dumps({"key": key, "state": state}) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False
