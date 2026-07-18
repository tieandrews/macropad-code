"""Starts each configured "session key" (config/keymap.yaml's `keys:`,
indices 0-5) inside a dedicated tmux session, and lets the bridge route
"action key" presses (indices 6-11) into whichever one is currently
selected on the MacroPad.

Why tmux: the physical MacroPad has no idea which terminal window or tab
you're looking at, and there is no cross-platform, reliable way to focus
"the right" window before sending a keystroke -- window-focus automation
is fragile and different on every OS/window manager. tmux sidesteps that
entirely: `tmux send-keys` injects text directly into a named session's
pane regardless of what's on screen, identically on Windows/WSL/Linux/
macOS. The cost is real: your agent sessions need to actually run inside
these tmux sessions for this to work at all -- see docs/customization.md.

For Claude Code specifically, each session is started with `claude
--remote-control`, so you can also review/steer it from claude.ai/code or
the Claude mobile app -- see
https://code.claude.com/docs/en/remote-control. That requires a Pro/Max/
Team/Enterprise plan and being logged in via `/login` (claude.ai OAuth,
not an API key).
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from . import config

SESSION_PREFIX = "macropad-key"
SESSION_KEY_COUNT = 6  # keys 0-5 -- must match firmware/code.py

# A dedicated tmux session whose single window is kept re-pointed (via
# `link-window`) at whichever session key is currently selected on the
# MacroPad. Attach to it once (`macropad-sessions --follow`) and leave it
# attached in a spare terminal/pane -- it live-updates as you rotate the
# encoder, no repeated manual `tmux attach`/detach needed. See
# sync_follow_session() below.
FOLLOW_SESSION = "macropad-follow"

# Matches Claude Code's own model names as they appear in its status line
# / welcome banner, e.g. "Opus 4.8", "Sonnet 4.5", "Haiku 4.5". Best-effort
# text scraping, same caveats as usage_pty.py -- if Claude Code's UI
# wording changes, this just stops matching and detect_model() returns
# None rather than something wrong.
_MODEL_RE = re.compile(r"\b(Opus|Sonnet|Haiku)\s*[\d.]*", re.IGNORECASE)


def session_name(key: int) -> str:
    return f"{SESSION_PREFIX}{key}"


def _tmux(*args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None


def tmux_available() -> bool:
    return _tmux("-V") is not None


def _has_named_session(name: str) -> bool:
    result = _tmux("has-session", "-t", name)
    return result is not None and result.returncode == 0


def has_session(key: int) -> bool:
    return _has_named_session(session_name(key))


def _launch_command(entry: dict, label: str) -> str:
    agent = entry.get("agent", "claude")
    if agent == "claude":
        return f"claude --remote-control {shlex.quote(label)}"
    if agent == "codex":
        print(
            "[macropad-sessions] note: Codex CLI has no equivalent of "
            "Claude Code's Remote Control yet -- starting a plain `codex` "
            "session in tmux. You can still use action keys to send it "
            "keystrokes, just not review it remotely.",
            flush=True,
        )
        return "codex"
    return agent  # anything else: treat it as a literal shell command


def start_session(key: int, entry: dict) -> bool:
    """Creates the tmux session for `key` (a keymap.yaml `keys:` entry)
    and launches its agent inside it, if not already running. Returns
    True if a session is running afterwards (whether newly started or
    already up), False on failure (no project_path, tmux missing, or
    tmux itself errored).

    Either way, pushes an initial "waiting" LED color for the key so it
    lights up immediately rather than staying dark until the agent's
    first hook event fires (UserPromptSubmit/Stop/etc. -- see
    macropad/hooks/claude_hook.py), which could be minutes away if you
    haven't typed anything into it yet. Re-running this against an
    already-running session (e.g. after a bridge or MacroPad restart)
    re-syncs the LED too, since the board itself doesn't persist state
    across a reboot."""
    from . import client

    if has_session(key):
        client.send_state(key, "waiting")
        return True

    project_path = entry.get("project_path")
    if not project_path:
        print(f"[macropad-sessions] key {key} has no project_path, skipping", flush=True)
        return False
    cwd = str(Path(project_path).expanduser())

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

    label = entry.get("label", name)
    command = _launch_command(entry, label)
    _tmux("send-keys", "-t", name, command, "Enter")
    print(f"[macropad-sessions] started {name} ({label}) in {cwd} -- running: {command}", flush=True)
    client.send_state(key, "waiting")
    return True


def start_all() -> None:
    keymap = config.load_keymap()
    keys = keymap.get("keys") or {}
    for key in sorted(keys):
        if key >= SESSION_KEY_COUNT:
            print(
                f"[macropad-sessions] skipping key {key}: only keys "
                f"0-{SESSION_KEY_COUNT - 1} are session slots (the rest are "
                "action keys) -- see docs/customization.md.",
                flush=True,
            )
            continue
        start_session(key, keys[key])


def send_to_session(key: int, text: str, send_enter: bool = True) -> bool:
    """Best-effort: injects `text` into the tmux session for `key`.
    Returns False (never raises) if that session isn't running, tmux
    isn't available, or both `text` is empty and `send_enter` is False --
    callers treat this the same as any other "nothing configured/running yet"
    no-op."""
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
    """Best-effort snapshot of key's tmux pane as plain text (current
    screen contents, not full scrollback). Returns None if that session
    isn't running or tmux isn't available."""
    if not has_session(key):
        return None
    result = _tmux("capture-pane", "-t", session_name(key), "-p")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def detect_model(key: int) -> Optional[str]:
    """Best-effort: scrapes the selected session's tmux pane for a
    Claude Code model name currently in use (e.g. "Sonnet 4.5"). Returns
    None if the session isn't running, or the pane doesn't currently
    show a recognizable model name (scrolled away, mid-redraw, Codex
    CLI which doesn't show one the same way, ...) -- never raises.

    Only looks at the last few lines of the visible pane, where Claude
    Code's status line actually lives -- otherwise this would just as
    happily match a model name mentioned in scrollback (a prior
    response, a `/model` command you just sent, ...), which is not
    what's currently selected."""
    text = capture_pane(key)
    if not text:
        return None
    # tmux pads captures to the pane's full height with blank lines, so
    # trim trailing blanks first -- otherwise "last few lines" would
    # usually just be empty padding rather than the status line.
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    tail = "\n".join(lines[-6:])
    # Take the *last* match in the tail, not the first -- Claude Code's
    # status line is the last thing rendered, so this favors it over an
    # older mention higher up in the same window (e.g. a model name
    # that appeared in a response a few lines back).
    matches = list(_MODEL_RE.finditer(tail))
    if not matches:
        return None
    return " ".join(matches[-1].group(0).split())


def attach(key: int) -> None:
    """Replaces the current process with `tmux attach` to key's session --
    for manually reviewing/typing in a session from any terminal."""
    import os

    name = session_name(key)
    if not has_session(key):
        raise SystemExit(f"No tmux session {name} running -- run `macropad-sessions` first.")
    os.execvp("tmux", ["tmux", "attach", "-t", name])


def sync_follow_session(key: int) -> bool:
    """Re-points FOLLOW_SESSION's one window at `key`'s session via `tmux
    link-window`, so anything already attached to FOLLOW_SESSION
    immediately starts showing the newly-selected session -- no
    detach/reattach needed. Called by the bridge every time the encoder
    selection changes (see bridge.py's _on_board_line). Safe to call even
    if nothing's attached to FOLLOW_SESSION (or it doesn't exist yet --
    it's created on first use) -- this is just bookkeeping either way.
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
    """Replaces the current process with `tmux attach` to FOLLOW_SESSION
    -- leave this running in a spare terminal/pane and it live-follows
    whichever session key is currently selected on the MacroPad (as long
    as `macropad-bridge` is running to keep calling sync_follow_session()
    -- see docs/running-sessions.md)."""
    import os

    if not _has_named_session(FOLLOW_SESSION):
        _tmux("new-session", "-d", "-s", FOLLOW_SESSION)
    os.execvp("tmux", ["tmux", "attach", "-t", FOLLOW_SESSION])


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Start (or attach to) the tmux + `claude --remote-control` "
            "sessions for each MacroPad session key (0-5) in config/keymap.yaml."
        )
    )
    parser.add_argument(
        "--attach",
        type=int,
        metavar="KEY",
        help="Attach to an already-running session's tmux pane instead of starting sessions.",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help=(
            "Attach to the shared macropad-follow session instead of starting sessions -- "
            "it live-follows whichever key is currently selected on the MacroPad, kept "
            "in sync by macropad-bridge. Leave this attached in a spare terminal/pane."
        ),
    )
    args = parser.parse_args()

    if args.attach is not None:
        attach(args.attach)
        return

    if args.follow:
        attach_follow()
        return

    if not tmux_available():
        raise SystemExit(
            "tmux is not installed or not on PATH -- install it first "
            "(e.g. `sudo apt install tmux`, `brew install tmux`; on Windows, "
            "run this from WSL)."
        )
    start_all()


if __name__ == "__main__":
    main()
