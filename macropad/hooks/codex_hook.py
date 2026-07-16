#!/usr/bin/env python3
"""Codex CLI notify entry point.

Wired into ~/.codex/config.toml by `macropad-setup` as:

    notify = ["python3", "/path/to/codex_hook.py"]

Codex invokes this after each completed turn with a single JSON string as
argv[1] (see https://developers.openai.com/codex/config-advanced). Unlike
Claude Code, Codex's `notify` currently only fires reliably for the
`agent-turn-complete` event -- there's no fine-grained "waiting on
permission" signal to hook into, so we can only light the pad's "done"
color and rely on the next `UserPromptSubmit`-equivalent (Codex has none
via notify) to change it. This is a known limitation of Codex CLI's hook
surface, not this integration.

Never raises and always exits 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macropad import client, config  # noqa: E402

EVENT_STATES = {
    "agent-turn-complete": "done",
}


def main() -> None:
    if len(sys.argv) < 2:
        return
    try:
        payload = json.loads(sys.argv[1])
    except (json.JSONDecodeError, ValueError):
        return

    state = EVENT_STATES.get(payload.get("type"))
    if state is None:
        return

    # Newer Codex builds include `cwd` in the payload; fall back to the
    # process's own working directory (the project Codex was run from)
    # when it's missing.
    cwd = payload.get("cwd") or os.getcwd()
    key = config.key_for_path(cwd)
    if key is None:
        return

    client.send_state(key, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
