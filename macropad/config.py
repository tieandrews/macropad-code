"""Load and query the human-editable YAML config in config/.

See docs/customization.md for the file formats.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
KEYMAP_PATH = CONFIG_DIR / "keymap.yaml"
COLORS_PATH = CONFIG_DIR / "colors.yaml"
BRIDGE_PATH = CONFIG_DIR / "bridge.yaml"
USAGE_PATH = CONFIG_DIR / "usage.yaml"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def load_keymap() -> dict:
    return _load_yaml(KEYMAP_PATH)


def load_colors() -> dict:
    return _load_yaml(COLORS_PATH)


def load_bridge_settings() -> dict:
    return _load_yaml(BRIDGE_PATH)


def load_usage_settings() -> dict:
    return _load_yaml(USAGE_PATH)


def _patch_top_level_scalar(path: Path, key: str, value: str) -> None:
    """Sets a top-level `key: value` line in a YAML file in place,
    preserving comments and everything else (a full YAML dump/reload
    would strip comments). Only matches unindented `key:` lines, so it's
    safe even if the same key name appears nested elsewhere in the file."""
    text = path.read_text()
    pattern = rf"^{re.escape(key)}:\s*\S+"
    patched, count = re.subn(pattern, f"{key}: {value}", text, count=1, flags=re.MULTILINE)
    if count == 0:
        patched = f"{key}: {value}\n" + text
    path.write_text(patched)


def set_usage_enabled(enabled: bool) -> None:
    _patch_top_level_scalar(USAGE_PATH, "enabled", "true" if enabled else "false")


def set_usage_source(source: str) -> None:
    _patch_top_level_scalar(USAGE_PATH, "source", source)


def set_usage_poll_interval(seconds: int) -> None:
    _patch_top_level_scalar(USAGE_PATH, "poll_interval_seconds", str(seconds))


def save_keymap(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(KEYMAP_PATH, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def key_for_path(cwd: Optional[str]) -> Optional[int]:
    """Return the macropad key index assigned to `cwd`, or None if no
    configured project_path matches. Matches the *longest* configured
    project_path that is an ancestor of (or equal to) cwd, so nested
    projects resolve to the more specific key."""
    if not cwd:
        return None
    keymap = load_keymap()
    try:
        cwd_resolved = str(Path(cwd).expanduser().resolve())
    except OSError:
        return None

    best_key: Optional[int] = None
    best_len = -1
    for key_str, entry in (keymap.get("keys") or {}).items():
        project_path = (entry or {}).get("project_path")
        if not project_path:
            continue
        resolved = str(Path(project_path).expanduser().resolve())
        if cwd_resolved == resolved or cwd_resolved.startswith(resolved.rstrip("/") + "/"):
            if len(resolved) > best_len:
                best_key = int(key_str)
                best_len = len(resolved)
    return best_key


def color_for_state(state: str) -> list:
    colors = load_colors()
    states = colors.get("states", {})
    brightness = colors.get("brightness", 1.0)
    entry = states.get(state) or states.get("idle") or {"color": [0, 0, 0]}
    r, g, b = entry["color"]
    return [round(r * brightness), round(g * brightness), round(b * brightness)]
