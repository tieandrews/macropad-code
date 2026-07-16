"""Optional integration with the community Claude-Code-Usage-Monitor tool
(https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor), if you
already run it. It watches Claude Code's usage in the background and (per
its own docs) can report Anthropic's actual session/weekly percentages --
the real number, not the local token-count estimate in usage.py, which
can't know your actual plan quota.

This integration is unverified against a live install: we don't maintain
that tool and its output file's exact schema isn't something we control or
have confirmed firsthand, so parsing here is deliberately defensive and
tries a few plausible shapes rather than assuming one. Any failure --
missing file, unexpected schema, tool not running -- returns None so the
caller falls back to the local estimate; this must never raise or block
the display.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_STATE_PATH = Path.home() / ".claude-monitor" / "state" / "latest.json"

# Plausible key names for each percentage, tried in order. If the real
# tool uses something else, add it here (see docs/customization.md).
_SESSION_KEYS = ("session_percent_used", "session_pct", "session_percentage", "session")
_WEEKLY_KEYS = ("weekly_percent_used", "weekly_pct", "weekly_percentage", "weekly")


def _find_pct(data: dict, keys) -> Optional[int]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return max(0, min(100, round(value)))
        if isinstance(value, dict):
            for sub_key in ("percent_used", "pct", "percentage", "used_percent"):
                sub_value = value.get(sub_key)
                if isinstance(sub_value, (int, float)) and not isinstance(sub_value, bool):
                    return max(0, min(100, round(sub_value)))
    return None


def read_monitor_percentages(state_path: Optional[str] = None) -> Optional[dict]:
    """Returns {"session_pct": int|None, "weekly_pct": int|None} if the
    monitor's state file exists and at least one percentage could be
    found, else None."""
    path = Path(state_path).expanduser() if state_path else DEFAULT_STATE_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    session_pct = _find_pct(data, _SESSION_KEYS)
    weekly_pct = _find_pct(data, _WEEKLY_KEYS)
    if session_pct is None and weekly_pct is None:
        return None
    return {"session_pct": session_pct, "weekly_pct": weekly_pct}
