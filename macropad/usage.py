"""Best-effort local Claude Code usage stats, read from Claude Code's own
session transcripts under ~/.claude/projects/.

Important honesty note: this is NOT an official Anthropic API and there is
no documented, stable way for a local script to read your exact plan quota
or rate-limit percentage. Claude Code's `/usage` command shows session
(~5h) and weekly (~7d) usage bars computed from local session history on
the machine it runs on -- but the underlying quota numbers it compares
against aren't published anywhere a script can read them.

What we *can* do reliably: sum the token counts Claude Code already
recorded for every assistant turn in its own transcripts, over a rolling
session window and a rolling weekly window. That gives an honest "how much
have I actually used" figure. Turning it into a percentage requires you to
supply your own estimated budget (config/usage.yaml) -- we never invent an
official-looking number we can't back up.

The transcript JSONL format is internal to Claude Code and can change
between releases (see docs/customization.md). Parsing here is deliberately
defensive: any line, file, or field we don't understand is skipped rather
than raising.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _parse_iso8601(timestamp: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _line_tokens_since(line: str, cutoff_epoch: float) -> int:
    line = line.strip()
    if not line:
        return 0
    try:
        entry = json.loads(line)
    except ValueError:
        return 0
    if not isinstance(entry, dict):
        return 0

    message = entry.get("message")
    if not isinstance(message, dict):
        return 0
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return 0

    entry_epoch = _parse_iso8601(entry.get("timestamp", ""))
    if entry_epoch is None or entry_epoch < cutoff_epoch:
        return 0

    return sum(usage.get(field, 0) or 0 for field in _USAGE_TOKEN_FIELDS)


def _sum_tokens_since(cutoff_epoch: float) -> int:
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return 0

    total = 0
    for jsonl_path in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
        try:
            # A file that hasn't been touched since the cutoff can't contain
            # any lines newer than the cutoff -- skip reading it entirely.
            if jsonl_path.stat().st_mtime < cutoff_epoch:
                continue
        except OSError:
            continue
        try:
            with open(jsonl_path, "r") as f:
                for line in f:
                    total += _line_tokens_since(line, cutoff_epoch)
        except OSError:
            continue
    return total


def compute_usage(session_window_hours: float = 5.0, weekly_window_days: float = 7.0) -> dict:
    """Returns raw token totals for the trailing session/weekly windows.
    Never raises -- an unreadable or unparseable transcript store just
    contributes 0."""
    now = time.time()
    try:
        session_tokens = _sum_tokens_since(now - session_window_hours * 3600)
        weekly_tokens = _sum_tokens_since(now - weekly_window_days * 86400)
    except Exception:
        session_tokens = weekly_tokens = 0
    return {
        "session_tokens": session_tokens,
        "weekly_tokens": weekly_tokens,
    }


def format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _pct(tokens: int, budget: Optional[int]) -> Optional[int]:
    if not budget:
        return None
    return max(0, min(100, round(tokens / budget * 100)))


def build_display_payload(settings: dict) -> dict:
    """Builds the {"usage": {...}} message the bridge sends over serial
    for the MacroPad's OLED, from config/usage.yaml settings."""
    session_hours = settings.get("session_window_hours", 5)
    weekly_days = settings.get("weekly_window_days", 7)
    computed = compute_usage(session_window_hours=session_hours, weekly_window_days=weekly_days)

    session_tokens = computed["session_tokens"]
    weekly_tokens = computed["weekly_tokens"]

    return {
        "usage": {
            "session": {
                "label": f"SESSION {session_hours:g}H",
                "value": format_count(session_tokens),
                "pct": _pct(session_tokens, settings.get("session_token_budget")),
            },
            "weekly": {
                "label": f"WEEK {weekly_days:g}D",
                "value": format_count(weekly_tokens),
                "pct": _pct(weekly_tokens, settings.get("weekly_token_budget")),
            },
        }
    }
