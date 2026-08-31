"""Interactive setup for macropad-code.

Walks you through:
  1. picking which coding agent(s) to wire up (Claude Code, Codex CLI)
  2. installing the matching hooks into their config files
  3. assigning MacroPad keys to project directories (config/keymap.yaml)
  4. optionally installing macropad-bridge as a background service

Run with: macropad-setup   (installed via pyproject.toml console_scripts)
or:       python -m macropad.setup_cli
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config

REPO_ROOT = config.REPO_ROOT
CLAUDE_HOOK = REPO_ROOT / "macropad" / "hooks" / "claude_hook.py"
CODEX_HOOK = REPO_ROOT / "macropad" / "hooks" / "codex_hook.py"
SERVICES_DIR = REPO_ROOT / "macropad" / "services"

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"

CLAUDE_HOOK_EVENTS = ["UserPromptSubmit", "PreToolUse", "Notification", "Stop", "SubagentStop"]


# ---------------------------------------------------------------------------
# small prompt helpers
# ---------------------------------------------------------------------------

def ask_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = input(question + suffix).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def ask_choice(question: str, options: list) -> int:
    """Prints a numbered menu, returns the chosen index (0-based)."""
    print(question)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")
    while True:
        answer = input(f"Enter a number [1-{len(options)}]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("Invalid choice, try again.")


def ask_text(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_suffix(path.suffix + f".bak-{stamp}")
        shutil.copy2(path, backup_path)
        print(f"  backed up {path} -> {backup_path}")


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------

def configure_claude() -> None:
    print("\n== Claude Code ==")
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if CLAUDE_SETTINGS_PATH.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            print(f"  {CLAUDE_SETTINGS_PATH} is not valid JSON -- fix or remove it "
                  "and re-run macropad-setup. Skipping Claude Code.")
            return
        backup(CLAUDE_SETTINGS_PATH)

    command = f"{sys.executable} {CLAUDE_HOOK}"
    hooks = settings.setdefault("hooks", {})
    for event in CLAUDE_HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        already_wired = any(
            h.get("command") == command
            for entry in entries
            for h in entry.get("hooks", [])
        )
        if already_wired:
            continue
        entries.append({"matcher": "", "hooks": [{"type": "command", "command": command}]})

    CLAUDE_SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  wired UserPromptSubmit / PreToolUse / Notification / Stop / SubagentStop "
          f"hooks into {CLAUDE_SETTINGS_PATH}")


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------

_NOTIFY_LINE_RE = re.compile(r"^\s*notify\s*=")
_TABLE_HEADER_RE = re.compile(r"^\s*\[")


def _patch_codex_notify(text: str, notify_line: str) -> str:
    """Insert/replace a top-level `notify = [...]` line in a TOML document
    without a full TOML parse (Codex's config.toml may have comments and
    formatting we don't want to disturb).

    TOML rule that matters here: once a `[table]` header appears, later
    `key = value` lines belong to *that table*, not the top level. So a
    top-level `notify` must live before the first `[table]` header --
    replacing an existing top-level notify line in place is safe;
    appending a new one must go before that header, not at EOF.
    """
    lines = text.splitlines()
    first_table_idx = len(lines)
    for i, line in enumerate(lines):
        if _TABLE_HEADER_RE.match(line):
            first_table_idx = i
            break

    for i in range(first_table_idx):
        if _NOTIFY_LINE_RE.match(lines[i]):
            lines[i] = notify_line
            return "\n".join(lines) + "\n"

    lines.insert(first_table_idx, notify_line)
    return "\n".join(lines) + "\n"


def configure_codex() -> None:
    print("\n== Codex CLI ==")
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    text = ""
    if CODEX_CONFIG_PATH.exists():
        text = CODEX_CONFIG_PATH.read_text()
        backup(CODEX_CONFIG_PATH)

    notify_line = f'notify = ["{sys.executable}", "{CODEX_HOOK}"]'
    text = _patch_codex_notify(text, notify_line)
    CODEX_CONFIG_PATH.write_text(text)
    print(f"  wired notify hook into {CODEX_CONFIG_PATH}")
    print("  note: Codex's notify only fires reliably on turn completion, so "
          "the pad will only show 'done' for Codex keys -- see docs/agent-integration.md.")


# ---------------------------------------------------------------------------
# keymap
# ---------------------------------------------------------------------------

def configure_keymap(agents: list) -> None:
    print("\n== Assign MacroPad keys ==")
    print("The MacroPad has 12 keys, numbered 0-11 (left-to-right, top-to-bottom).")
    if not ask_yes_no("Assign one or more keys to a project now?", default=True):
        return

    keymap = config.load_keymap()
    keys = keymap.setdefault("keys", {})

    while True:
        key_str = ask_text("Key index (0-11)")
        if not key_str.isdigit() or not (0 <= int(key_str) <= 11):
            print("  must be a number 0-11.")
            continue
        label = ask_text("Label for this key", default=f"project-{key_str}")
        agent_idx = ask_choice("Which agent runs here?", agents)
        agent = agents[agent_idx]
        project_path = ask_text("Project directory (absolute or ~-relative path)")
        resolved = Path(project_path).expanduser()
        if not resolved.is_dir():
            print(f"  warning: {resolved} doesn't exist (yet) -- saving it anyway.")

        keys[key_str] = {"label": label, "agent": agent, "project_path": project_path}
        print(f"  key {key_str} -> {label} ({agent}, {project_path})")

        if not ask_yes_no("Assign another key?", default=False):
            break

    keymap["keys"] = keys
    config.save_keymap(keymap)
    print(f"  saved {config.KEYMAP_PATH}")


# ---------------------------------------------------------------------------
# usage display
# ---------------------------------------------------------------------------

def configure_usage_display() -> None:
    print("\n== Usage display (OLED) ==")
    print("The MacroPad's screen can show your Claude Code token usage. There are "
          "three ways to get the numbers -- see docs/customization.md for the full "
          "tradeoffs:")
    print("  1. Local estimate -- always works, zero extra setup. Shows raw token "
          "counts (or a % if you set your own budget). Not Anthropic's real quota.")
    print("  2. claude-monitor -- real Anthropic percentages, but only if you already "
          "run the community Claude-Code-Usage-Monitor tool.")
    print("  3. Live claude /usage (experimental) -- real percentages by periodically "
          "launching `claude` itself in the background. Heavier, more fragile, and "
          "needs `claude` already logged in with the working directory already "
          "trusted -- see docs/customization.md before picking this.")

    if not ask_yes_no("Enable the OLED usage display?", default=True):
        config.set_usage_enabled(False)
        print("  disabled.")
        return

    source_idx = ask_choice("Which usage source?", [
        "Local estimate (recommended default)",
        "claude-monitor (only if you already run it)",
        "Live claude /usage (experimental)",
    ])
    source = ["local_estimate", "claude_monitor", "claude_pty"][source_idx]

    config.set_usage_enabled(True)
    config.set_usage_source(source)

    if source == "claude_pty":
        print("  claude_pty selected -- before this will work:")
        print("    1. Run `claude` by hand once from the directory in "
              "config/usage.yaml's claude_pty.working_dir (your home directory by "
              "default) and get all the way to a normal chat prompt -- that clears "
              "both the onboarding wizard and any directory-trust prompt.")
        print("    2. It should go straight to a chat, not a login/theme screen, on "
              "every future launch.")
        print("  Also raising poll_interval_seconds to 600s -- this launches a full "
              "`claude` process every poll, so keep it infrequent. Edit "
              "config/usage.yaml to change it.")
        config.set_usage_poll_interval(600)
        print("  Note: this needs the optional `pyte` dependency for reliable "
              "parsing -- run `uv sync --extra pty` (or `pip install pyte`).")

    print(f"  usage display: enabled, source={source} -- see config/usage.yaml to tune "
          "windows, budgets, or source-specific settings.")


# ---------------------------------------------------------------------------
# background service
# ---------------------------------------------------------------------------

def _write_template(template_name: str, dest: Path, replacements: dict) -> None:
    text = (SERVICES_DIR / template_name).read_text()
    for token, value in replacements.items():
        text = text.replace(token, value)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def install_service() -> None:
    print("\n== Background service ==")
    print("macropad-bridge needs to run continuously to relay agent events to the "
          "MacroPad's LEDs. It can start automatically at login.")
    if not ask_yes_no("Install macropad-bridge as a background service now?", default=True):
        print("  skipped -- run `macropad-bridge` manually whenever you want the "
              "lights active, or see docs/running-the-bridge.md to install this later.")
        return

    system = platform.system()
    replacements = {"__PYTHON__": sys.executable, "__REPO_ROOT__": str(REPO_ROOT)}

    if system == "Darwin":
        dest = Path.home() / "Library" / "LaunchAgents" / "com.macropad-code.bridge.plist"
        log_dir = REPO_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        replacements["__LOG_DIR__"] = str(log_dir)
        _write_template("launchd.plist.template", dest, replacements)
        print(f"  wrote {dest}")
        if ask_yes_no("Load it now with launchctl?", default=True):
            subprocess.run(["launchctl", "load", "-w", str(dest)], check=False)
            print("  loaded. Check `launchctl list | grep macropad-code` to confirm.")

    elif system == "Linux":
        dest = Path.home() / ".config" / "systemd" / "user" / "macropad-bridge.service"
        _write_template("systemd.service.template", dest, replacements)
        print(f"  wrote {dest}")
        if ask_yes_no("Enable and start it now with systemctl --user?", default=True):
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", "macropad-bridge.service"],
                            check=False)
            print("  enabled. Check `systemctl --user status macropad-bridge` to confirm.")

    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(Path.home()))) / "macropad-code"
        dest = appdata / "macropad_bridge_launcher.pyw"
        _write_template("windows_launcher.pyw.template", dest, replacements)
        print(f"  wrote {dest}")
        print("  Windows startup isn't fully automated here. To finish: press Win+R, "
              "run `shell:startup`, and drop a shortcut to that .pyw file in the folder "
              "that opens. See docs/running-the-bridge.md for details.")

    else:
        print(f"  unrecognized platform '{system}' -- see docs/running-the-bridge.md "
              "for how to run macropad-bridge as a background process manually.")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("macropad-code setup")
    print("====================")
    print(f"Repo: {REPO_ROOT}\n")

    agent_options = ["Claude Code", "Codex CLI", "Both"]
    choice = ask_choice("Which coding agent(s) do you want to hook up to the MacroPad?",
                         agent_options)

    configured_agents = []
    if choice in (0, 2):
        configure_claude()
        configured_agents.append("claude")
    if choice in (1, 2):
        configure_codex()
        configured_agents.append("codex")

    configure_keymap(configured_agents)
    if "claude" in configured_agents:
        configure_usage_display()
    install_service()

    print("\n== Done ==")
    print("Next steps:")
    print("  1. Flash the MacroPad -- see docs/hardware-setup.md")
    print("  2. If you skipped the background service, run `macropad-bridge` in a terminal")
    print("  3. Edit config/keymap.yaml, config/colors.yaml, or config/usage.yaml any time -- "
          "see docs/customization.md")
    print("  4. Optional: rotate the encoder to select a session and use the bottom two rows "
          "of keys (6-11) to send input into it (including switching models). Configure what "
          "each one sends in config/keymap.yaml's `actions:` section, then run "
          "`uv run macropad-sessions` (needs tmux installed, or herdr if you set "
          "config/bridge.yaml's `session_backend: herdr`) to start each session in its own "
          "pane -- see docs/customization.md for the full setup.")
    print("  5. Optional: press the encoder itself to speak a command instead of typing it -- "
          "set voice.enabled: true in config/bridge.yaml (needs `uv sync --extra voice` and a "
          "reachable microphone). See docs/customization.md.")


if __name__ == "__main__":
    main()
