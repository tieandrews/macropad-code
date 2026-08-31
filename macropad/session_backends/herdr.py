"""herdr backend for macropad/sessions.py.

herdr (https://herdr.dev) is a terminal multiplexer purpose-built for
running coding agents, with a CLI + local socket API designed for exactly
this kind of external orchestration -- see docs/running-sessions.md.

Mapping onto herdr's own vocabulary (session > workspace > tab > pane): a
dedicated named herdr session (HERDR_SESSION, "macropad") holds one
workspace per configured session key. Each workspace's single root pane
is where the configured agent actually runs. Using a *named* herdr
session, rather than the user's own default one, keeps these workspaces
out of the way of whatever the user's already doing in herdr day to
day -- the same reason the tmux backend prefixes its own session names
instead of reusing whatever's running.

Unlike the tmux backend (whose session names, e.g. "macropad-key0", are
also the thing tmux itself displays), a workspace's herdr *label* is
purely cosmetic -- herdr's own sidebar is what you actually look at day
to day, so _workspace_label() below includes the configured project
label too (e.g. "mp0-macropad-code") rather than just the key. Key ->
workspace resolution (_workspace_id_for_key()) only relies on the
stable "mp{key}-" prefix, so the project-name suffix is free to vary
without breaking lookups.

Unlike tmux, herdr already tracks one "focused" workspace per session and
pushes focus changes live to any client attached to that session --
confirmed by attaching a client and calling `herdr workspace focus`
from a separate process while watching it update (herdr 0.8.2). So unlike
the tmux backend, which needs a dedicated FOLLOW_SESSION kept re-pointed
via `link-window`, the "follow" view here is just the "macropad" session
itself, with sync_follow_session() keeping its focus in sync.

A named session's server isn't auto-started the way tmux's server is by
`new-session -d` -- it has to be started explicitly first
(`herdr --session macropad server`, headless/backgrounded here via
_ensure_server_running()) before any workspace/pane command against it
will work.

config/keymap.yaml's `send_keys:` entries use tmux's own send-keys
syntax (documented in docs/customization.md), since that's what the
tmux backend passes straight through unchanged -- tmux's send-keys
itself auto-detects whether a given argument is literal text or one of
its own recognized key names (e.g. "C-c", "Escape"). herdr has no such
single command: `pane send-text` always types literally and `pane
send-keys` always expects its own key-name syntax (e.g. "ctrl+c",
"esc"). _translate_key_name() below bridges that gap by recognizing
tmux's key-name syntax and translating it, so the same keymap.yaml
config works unchanged under either backend -- see send_to_session().
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Optional

from . import SESSION_KEY_COUNT, session_name

INSTALL_HINT = "install it first (e.g. `brew install herdr`, or see https://herdr.dev/docs/install/)."

# tmux send-keys names for keys that aren't a simple C-<char>/M-<char>/F<n>
# pattern -- mapped to herdr's own `pane send-keys` names. Not exhaustive
# (tmux recognizes more), just the ones plausible for a keymap.yaml
# send_keys: entry.
_TMUX_KEY_ALIASES = {
    "enter": "enter", "escape": "esc", "tab": "tab", "btab": "shift+tab",
    "space": "space", "bspace": "backspace", "delete": "delete",
    "end": "end", "home": "home", "insert": "insert",
    "pageup": "pageup", "ppage": "pageup", "pagedown": "pagedown", "npage": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
}


def _translate_key_name(text: str) -> Optional[str]:
    """Best-effort translation of a tmux-style send-keys key name into
    herdr's `pane send-keys` syntax. Returns None if `text` doesn't look
    like a recognized key name at all -- callers then treat it as
    literal text instead (`pane send-text`/`pane run`), matching what
    tmux itself does for anything that isn't one of its own recognized
    names."""
    lowered = text.lower()
    match = re.fullmatch(r"c-([a-z])", lowered)
    if match:
        return f"ctrl+{match.group(1)}"
    match = re.fullmatch(r"m-([a-z])", lowered)
    if match:
        return f"alt+{match.group(1)}"
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", lowered):
        return lowered
    return _TMUX_KEY_ALIASES.get(lowered)

# Named herdr session housing all of our workspaces -- see module
# docstring for why this is a dedicated named session rather than the
# user's own default one.
HERDR_SESSION = "macropad"

_SERVER_START_POLL_ATTEMPTS = 20
_SERVER_START_POLL_INTERVAL_SECONDS = 0.25


def available() -> bool:
    try:
        result = subprocess.run(["herdr", "--version"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _herdr(*args: str) -> Optional[subprocess.CompletedProcess]:
    """Runs `herdr <args> --session macropad` -- for everything *except*
    the `herdr session ...` subcommands themselves, which take the
    session name as a positional argument instead (see _server_running()
    and _ensure_server_running())."""
    try:
        return subprocess.run(["herdr", *args, "--session", HERDR_SESSION], capture_output=True, text=True)
    except FileNotFoundError:
        return None


def _server_running() -> bool:
    try:
        result = subprocess.run(["herdr", "session", "list", "--json"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return False
    for entry in data.get("sessions", []):
        if entry.get("name") == HERDR_SESSION:
            return bool(entry.get("running"))
    return False


def _ensure_server_running() -> bool:
    """Starts the headless server for HERDR_SESSION if it isn't already
    running (analogous to tmux auto-starting its server on `new-session
    -d`, which herdr's named sessions don't do implicitly). Returns True
    once it's confirmed up, False if herdr isn't installed or it never
    comes up within the poll budget."""
    if _server_running():
        return True
    try:
        subprocess.Popen(
            ["herdr", "--session", HERDR_SESSION, "server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False
    for _ in range(_SERVER_START_POLL_ATTEMPTS):
        time.sleep(_SERVER_START_POLL_INTERVAL_SECONDS)
        if _server_running():
            return True
    return False


def _workspace_label_prefix(key: int) -> str:
    """The stable part of key's workspace label -- see _workspace_label()
    and the module docstring. "mp0-" through "mp5-" (SESSION_KEY_COUNT is
    6) are never a prefix of one another, so a plain startswith() below
    is an unambiguous match with no risk of key 0 matching key 1's
    workspace too."""
    return f"mp{key}-"


def _workspace_label(key: int, repo_label: str) -> str:
    """The full herdr workspace label for `key`: the stable prefix plus
    the configured project label, so herdr's own sidebar shows which
    repo each workspace is (e.g. "mp0-macropad-code") instead of just a
    bare key index."""
    return f"{_workspace_label_prefix(key)}{repo_label}"


def _workspace_id_for_key(key: int) -> Optional[str]:
    """Resolves key's herdr workspace_id by its label's "mp{key}-"
    prefix (see _workspace_label_prefix()), or None if no such workspace
    exists yet (including if the "macropad" session's server isn't
    running at all -- `workspace list` just fails cleanly in that case).
    Takes the first match -- fine as long as start() always checks
    has_session() first, same guarantee the tmux backend relies on for
    its session names."""
    result = _herdr("workspace", "list")
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    prefix = _workspace_label_prefix(key)
    for workspace in data.get("result", {}).get("workspaces", []):
        if (workspace.get("label") or "").startswith(prefix):
            return workspace.get("workspace_id")
    return None


def has_session(key: int) -> bool:
    return _workspace_id_for_key(key) is not None


def _pane_id_for_key(key: int) -> Optional[str]:
    workspace_id = _workspace_id_for_key(key)
    if workspace_id is None:
        return None
    result = _herdr("pane", "list", "--workspace", workspace_id)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    panes = data.get("result", {}).get("panes", [])
    return panes[0]["pane_id"] if panes else None


def start(key: int, cwd: str, command: str, label: str) -> bool:
    """Creates the herdr workspace for `key` (its herdr label built from
    `label` -- the configured project label, see _workspace_label()) and
    runs `command` in its root pane. Returns True if the workspace exists
    and the command was sent, False on failure (herdr missing, the
    "macropad" session's server never came up, or workspace creation
    itself errored)."""
    if not _ensure_server_running():
        print(
            "[macropad-sessions] failed to start the herdr 'macropad' session "
            "server -- is herdr installed? (`herdr --version`)",
            flush=True,
        )
        return False
    workspace_label = _workspace_label(key, label)
    created = _herdr("workspace", "create", "--cwd", cwd, "--label", workspace_label, "--no-focus")
    if created is None or created.returncode != 0:
        detail = (created.stderr or created.stdout).strip() if created else ""
        print(f"[macropad-sessions] failed to create herdr workspace {workspace_label}: {detail}", flush=True)
        return False
    try:
        pane_id = json.loads(created.stdout)["result"]["root_pane"]["pane_id"]
    except (ValueError, KeyError):
        print(
            f"[macropad-sessions] herdr workspace create for {workspace_label} returned "
            f"unexpected output: {created.stdout.strip()}",
            flush=True,
        )
        return False
    run = _herdr("pane", "run", pane_id, command)
    return run is not None and run.returncode == 0


def send_to_session(key: int, text: str, send_enter: bool = True) -> bool:
    if not (0 <= key < SESSION_KEY_COUNT):
        return False
    if not text and not send_enter:
        return False
    pane_id = _pane_id_for_key(key)
    if pane_id is None:
        return False

    key_name = _translate_key_name(text) if text else None
    if key_name is not None:
        # A recognized key name (e.g. keymap.yaml's `send_keys: "C-c"`) --
        # send it via herdr's own key-name syntax, not as literal text.
        # See module docstring / _translate_key_name().
        keys = [key_name] + (["enter"] if send_enter else [])
        result = _herdr("pane", "send-keys", pane_id, *keys)
    elif text and send_enter:
        result = _herdr("pane", "run", pane_id, text)
    elif text:
        result = _herdr("pane", "send-text", pane_id, text)
    else:
        result = _herdr("pane", "send-keys", pane_id, "enter")
    return result is not None and result.returncode == 0


def capture_pane(key: int) -> Optional[str]:
    pane_id = _pane_id_for_key(key)
    if pane_id is None:
        return None
    result = _herdr("pane", "read", pane_id, "--source", "visible")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def attach(key: int) -> None:
    """Focuses key's workspace, then replaces the current process with
    `herdr session attach macropad` -- for manually reviewing/typing in a
    session from any terminal."""
    import os

    workspace_id = _workspace_id_for_key(key)
    if workspace_id is None:
        raise SystemExit(
            f"No herdr workspace for {session_name(key)} (label prefix "
            f"{_workspace_label_prefix(key)!r}) running -- run `macropad-sessions` first."
        )
    focus = _herdr("workspace", "focus", workspace_id)
    if focus is None or focus.returncode != 0:
        raise SystemExit(f"Failed to focus herdr workspace {workspace_id} ({session_name(key)}).")
    os.execvp("herdr", ["herdr", "session", "attach", HERDR_SESSION])


def sync_follow_session(key: int) -> bool:
    """Focuses key's workspace within the "macropad" session -- herdr
    pushes that live to any client already attached to it (see module
    docstring), so this alone is enough to keep an attached follow view in
    sync, no separate link/re-point step needed. Returns False (never
    raises) if `key` has no running session, or if herdr itself isn't
    available."""
    workspace_id = _workspace_id_for_key(key)
    if workspace_id is None:
        return False
    result = _herdr("workspace", "focus", workspace_id)
    return result is not None and result.returncode == 0


def attach_follow() -> None:
    """Replaces the current process with `herdr session attach macropad`
    -- leave this running in a spare terminal/pane and it live-follows
    whichever session key is currently selected on the MacroPad, kept in
    sync by sync_follow_session() above."""
    import os

    _ensure_server_running()
    os.execvp("herdr", ["herdr", "session", "attach", HERDR_SESSION])
