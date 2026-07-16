#!/usr/bin/env python3
"""Claude Code hook entry point.

Wired into ~/.claude/settings.json by `macropad-setup` for the
UserPromptSubmit, Notification, Stop, and SubagentStop events. Claude Code
invokes this once per matching event and pipes a JSON payload to stdin,
documented at https://code.claude.com/docs/en/hooks -- the fields we care
about are `hook_event_name`, `cwd`, and (for Notification)
`notification_type`.

Never raises and always exits 0: a lit LED is a nice-to-have, not
something that should ever interrupt, block, or slow down an agent turn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macropad import client, config  # noqa: E402

# Notification hooks carry a `notification_type` field distinguishing why
# Claude Code is pinging you. We only act on the ones with an obvious
# color; anything else (auth_success, elicitation_*, ...) is ignored.
NOTIFICATION_STATES = {
    "permission_prompt": "permission",
    "idle_prompt": "waiting",
    "agent_needs_input": "waiting",
}

EVENT_STATES = {
    "UserPromptSubmit": "working",
    "Stop": "done",
    "SubagentStop": "done",
}


def resolve_state(payload: dict) -> str | None:
    event = payload.get("hook_event_name")
    if event == "Notification":
        return NOTIFICATION_STATES.get(payload.get("notification_type"))
    return EVENT_STATES.get(event)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    state = resolve_state(payload)
    if state is None:
        return

    key = config.key_for_path(payload.get("cwd"))
    if key is None:
        return

    client.send_state(key, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fire-and-forget: never let a bridge/config problem surface as a
        # hook failure inside a Claude Code session.
        pass
