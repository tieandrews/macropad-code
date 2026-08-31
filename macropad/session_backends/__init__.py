"""Pluggable backends for macropad/sessions.py's multi-project session
management -- one implementation per supported terminal multiplexer,
selected at runtime via config/bridge.yaml's `session_backend` (tmux or
herdr). See macropad/sessions.py for the shared public API (detect_model,
detect_branch, start_session, etc.) built on top of whichever backend is
selected, and docs/running-sessions.md for how the two compare.

Every backend module exposes the same functions:
  available() -> bool
  has_session(key: int) -> bool
  start(key: int, cwd: str, command: str, label: str) -> bool
      `label` is the configured project label (keymap.yaml's `label:`,
      falling back to session_name(key)) -- purely cosmetic input a
      backend may use however it likes (herdr folds it into the
      workspace's own label so its sidebar shows which repo is which;
      tmux ignores it, since a tmux session's name *is* session_name(key)
      already and isn't meant to change).
  send_to_session(key: int, text: str, send_enter: bool) -> bool
  capture_pane(key: int) -> Optional[str]
  attach(key: int) -> None          -- replaces the current process
  sync_follow_session(key: int) -> bool
  attach_follow() -> None           -- replaces the current process
and the constant INSTALL_HINT (a human-readable string for `main()`'s
"not installed" error).
"""
from __future__ import annotations

SESSION_PREFIX = "macropad-key"
SESSION_KEY_COUNT = 6  # keys 0-5 -- must match firmware/code.py

VALID_SESSION_BACKENDS = ("tmux", "herdr")
DEFAULT_SESSION_BACKEND = "tmux"


def session_name(key: int) -> str:
    return f"{SESSION_PREFIX}{key}"


def get_backend(name: str):
    """Lazily imports and returns the backend module for `name` (one of
    VALID_SESSION_BACKENDS). Lazy purely so that importing this package
    doesn't require both backends' CLIs to exist -- in practice neither
    backend module has any Python dependency of its own, they just shell
    out to their respective CLI, but this keeps the door open if that
    ever changes."""
    if name == "tmux":
        from . import tmux
        return tmux
    if name == "herdr":
        from . import herdr
        return herdr
    raise SystemExit(
        f"[macropad-sessions] unknown session_backend {name!r} in "
        f"config/bridge.yaml -- expected one of {', '.join(VALID_SESSION_BACKENDS)}."
    )
