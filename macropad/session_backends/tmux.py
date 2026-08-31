"""tmux backend for macropad/sessions.py.

Why tmux: the physical MacroPad has no idea which terminal window or tab
you're looking at, and there is no cross-platform, reliable way to focus
"the right" window before sending a keystroke -- window-focus automation
is fragile and different on every OS/window manager. tmux sidesteps that
entirely: `tmux send-keys` injects text directly into a named session's
pane regardless of what's on screen, identically on Windows/WSL/Linux/
macOS. The cost is real: your agent sessions need to actually run inside
these tmux sessions for this to work at all -- see docs/customization.md.
"""
from __future__ import annotations

import subprocess
from typing import Optional

from . import SESSION_KEY_COUNT, session_name

INSTALL_HINT = (
    "install it first (e.g. `sudo apt install tmux`, `brew install tmux`; "
    "on Windows, run this from WSL)."
)

# A dedicated tmux session whose single window is kept re-pointed (via
# `link-window`) at whichever session key is currently selected on the
# MacroPad. Attach to it once (`macropad-sessions --follow`) and leave it
# attached in a spare terminal/pane -- it live-updates as you rotate the
# encoder, no repeated manual `tmux attach`/detach needed. See
# sync_follow_session() below.
FOLLOW_SESSION = "macropad-follow"


def _tmux(*args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None


def available() -> bool:
    return _tmux("-V") is not None


def _has_named_session(name: str) -> bool:
    result = _tmux("has-session", "-t", name)
    return result is not None and result.returncode == 0


def has_session(key: int) -> bool:
    return _has_named_session(session_name(key))


def start(key: int, cwd: str, command: str, label: str) -> bool:
    """Creates the tmux session for `key` and types `command` into it.
    Returns True if the session exists and the command was sent, False on
    failure (tmux missing, or tmux itself errored creating the session).

    `label` (the configured project label) is unused here -- a tmux
    session's name *is* session_name(key) already (e.g. "macropad-key0"),
    unlike herdr's workspace label, which is purely cosmetic and folds
    `label` in so herdr's own sidebar shows which repo is which -- see
    session_backends/herdr.py."""
    name = session_name(key)
    created = _tmux("new-session", "-d", "-s", name, "-c", cwd)
    if created is None:
        print("[macropad-sessions] tmux is not installed or not on PATH", flush=True)
        return False
    if created.returncode != 0:
        print(
            f"[macropad-sessions] failed to create tmux session {name}: "
            f"{created.stderr.strip()}",
            flush=True,
        )
        return False
    _tmux("send-keys", "-t", name, command, "Enter")
    return True


def send_to_session(key: int, text: str, send_enter: bool = True) -> bool:
    if not (0 <= key < SESSION_KEY_COUNT):
        return False
    if not text and not send_enter:
        return False
    if not has_session(key):
        return False
    args = ["send-keys", "-t", session_name(key)]
    if text:
        args.append(text)
    if send_enter:
        args.append("Enter")
    result = _tmux(*args)
    return result is not None and result.returncode == 0


def capture_pane(key: int) -> Optional[str]:
    if not has_session(key):
        return None
    result = _tmux("capture-pane", "-t", session_name(key), "-p")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def attach(key: int) -> None:
    """Replaces the current process with `tmux attach` to key's session."""
    import os

    name = session_name(key)
    if not has_session(key):
        raise SystemExit(f"No tmux session {name} running -- run `macropad-sessions` first.")
    os.execvp("tmux", ["tmux", "attach", "-t", name])


def sync_follow_session(key: int) -> bool:
    """Re-points FOLLOW_SESSION's one window at `key`'s session via `tmux
    link-window`, so anything already attached to FOLLOW_SESSION
    immediately starts showing the newly-selected session -- no
    detach/reattach needed. Safe to call even if nothing's attached to
    FOLLOW_SESSION (or it doesn't exist yet -- it's created on first use).
    Returns False (never raises) if `key` has no running session, or if
    tmux itself isn't available."""
    if not has_session(key):
        return False
    if not _has_named_session(FOLLOW_SESSION):
        created = _tmux("new-session", "-d", "-s", FOLLOW_SESSION)
        if created is None or created.returncode != 0:
            return False
    result = _tmux("link-window", "-k", "-s", f"{session_name(key)}:0", "-t", f"{FOLLOW_SESSION}:0")
    return result is not None and result.returncode == 0


def attach_follow() -> None:
    """Replaces the current process with `tmux attach` to FOLLOW_SESSION."""
    import os

    if not _has_named_session(FOLLOW_SESSION):
        _tmux("new-session", "-d", "-s", FOLLOW_SESSION)
    os.execvp("tmux", ["tmux", "attach", "-t", FOLLOW_SESSION])
