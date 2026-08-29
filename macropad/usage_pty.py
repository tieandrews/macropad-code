"""EXPERIMENTAL: get Claude's real session/weekly usage percentages by
driving an actual `claude` process through a pseudo-terminal and reading
its `/usage` panel -- the only way to get Anthropic's real percentages,
since there's no API for them (see usage.py's docstring).

This is meaningfully heavier and more fragile than the other sources, and
you should understand the tradeoffs before enabling it (config/usage.yaml,
`source: claude_pty`):

  - It launches a full `claude` process on a timer. That's real CPU/
    memory/startup cost, repeated every poll interval -- keep the interval
    long (minutes, not seconds; macropad-setup defaults it to 600s when
    you pick this source).
  - It only works if `claude` is already logged in *and* the working
    directory it launches into is already trusted by Claude Code. We
    detected this directly: launching `claude` fresh into an
    unauthenticated or untrusted context lands on the onboarding wizard
    (theme picker, "select login method") or a directory-trust prompt --
    not a ready session -- and there is no way to click through a trust
    prompt from here without defeating the point of that prompt. So
    instead of guessing, we watch for those exact screens and bail out
    immediately with a clear log message rather than hang. Run `claude`
    by hand once from `claude_pty.working_dir` (config/usage.yaml) to
    clear both before enabling this.
  - It scrapes `/usage`'s rendered terminal text, not a documented data
    format -- that's a human-facing UI that can change wording or layout
    in any Claude Code release. Verified against Claude Code v2.1.212's
    real, logged-in `/usage` panel; the percentage regexes below match
    "Current session ... N% used" / "Current week (all models) ... N%
    used". If wording changes in a future release, this source silently
    returns None and the bridge falls back to the local token-count
    estimate rather than showing stale or wrong numbers -- it will never
    raise or hang the bridge.
  - Requires the optional `pyte` dependency (`uv sync --extra pty`) to
    turn the raw ANSI terminal stream into readable text; without it, a
    much cruder regex-based ANSI stripper is used as a fallback.
  - `claude_command` (config/usage.yaml) must be resolvable from wherever
    this runs. If the bridge runs as a `systemd --user` service, its PATH
    is minimal and won't include tool-manager install dirs (linuxbrew,
    nvm, etc.) -- a bare "claude" then fails to spawn, and since this
    function never raises, that failure is silent and just looks like
    permanent fallback to local_estimate. Use an absolute path
    (`which claude`) if you see that.
"""
from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

# Screens that mean "not a ready, usable session" -- if we see any of
# these, stop immediately rather than wait out the timeout or (worse)
# act on garbage text.
_BLOCKED_MARKERS = (
    "Select login method",
    "Choose the text style",
    "trust the files in this folder",
    "Do you trust the files",
    "Anthropic Console account",
)

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*(\x07|\x1b\\)")
_ANSI_CHARSET_RE = re.compile(r"\x1b[()][0-9A-Za-z]")

_SESSION_PCT_RE = re.compile(r"session[^\d%]{0,40}?(\d{1,3})\s*%", re.IGNORECASE)
_WEEKLY_PCT_RE = re.compile(r"week(?:ly)?[^\d%]{0,40}?(\d{1,3})\s*%", re.IGNORECASE)


def _read_for(master_fd: int, seconds: float) -> bytes:
    chunks = []
    end = time.time() + seconds
    while time.time() < end:
        remaining = max(0.0, end - time.time())
        r, _, _ = select.select([master_fd], [], [], min(0.2, remaining) or 0.01)
        if r:
            try:
                data = os.read(master_fd, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def _strip_ansi(raw: str) -> str:
    text = _ANSI_CSI_RE.sub("", raw)
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CHARSET_RE.sub("", text)
    return text


class _Terminal:
    """Wraps a pyte screen/stream pair so terminal state (cursor position,
    prior redraws, etc.) persists across multiple feed() calls -- claude's
    TUI often repaints incrementally rather than re-sending a full screen
    on every update, so re-creating the screen per read loses content."""

    def __init__(self) -> None:
        self._pyte = None
        try:
            import pyte
            self._pyte = pyte
            self.screen = pyte.Screen(160, 60)
            self.stream = pyte.Stream(self.screen)
        except ImportError:
            self._raw = ""

    def feed(self, raw: bytes) -> str:
        decoded = raw.decode("utf-8", errors="ignore")
        if self._pyte is None:
            self._raw += decoded
            return _strip_ansi(self._raw)
        self.stream.feed(decoded)
        return "\n".join(self.screen.display)


def _extract_percentages(text: str) -> Optional[dict]:
    # pyte pads every line out to the screen's full column width, so the
    # raw rendered text has runs of dozens of spaces between a label like
    # "Current session" and its value on the line below -- collapse all
    # whitespace (including newlines) first so the regexes' gap limits are
    # measuring real intervening content, not screen padding.
    normalized = re.sub(r"\s+", " ", text)
    session_match = _SESSION_PCT_RE.search(normalized)
    weekly_match = _WEEKLY_PCT_RE.search(normalized)
    if not session_match and not weekly_match:
        return None
    return {
        "session_pct": int(session_match.group(1)) if session_match else None,
        "weekly_pct": int(weekly_match.group(1)) if weekly_match else None,
    }


def _kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def get_usage_percentages(claude_command: str = "claude",
                           working_dir: Optional[str] = None,
                           timeout_seconds: float = 20.0) -> Optional[dict]:
    """Launches `claude`, sends /usage, and returns real percentages.
    Never raises -- returns None on any failure (not logged in, directory
    not trusted, claude not installed, parsing came up empty, timed out,
    ...); see the module docstring for why each of those is a real,
    expected possibility rather than a bug."""
    ready_timeout = max(2.0, timeout_seconds * 0.4)
    usage_timeout = max(2.0, timeout_seconds * 0.6)
    cwd = str(Path(working_dir).expanduser()) if working_dir else str(Path.home())

    master_fd, slave_fd = pty.openpty()
    process = None
    try:
        process = subprocess.Popen(
            [claude_command],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1

        terminal = _Terminal()
        boot_text = terminal.feed(_read_for(master_fd, ready_timeout))
        if any(marker in boot_text for marker in _BLOCKED_MARKERS):
            return None

        os.write(master_fd, b"/usage\r")
        usage_text = terminal.feed(_read_for(master_fd, usage_timeout))
        if any(marker in usage_text for marker in _BLOCKED_MARKERS):
            return None

        return _extract_percentages(usage_text)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if slave_fd != -1:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        if process is not None:
            _kill(process)
