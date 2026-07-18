"""macropad-webui: a local web UI for editing config/colors.yaml,
config/keymap.yaml, and config/bridge.yaml without hand-editing YAML.

Edits happen against named "versions" under config-versions/<name>/ --
full copies of the three editable files -- so you can draft changes
without touching the live config the bridge is reading. Nothing reaches
config/ until you hit "Apply to pad", which copies the selected
version's files over config/*.yaml and best-effort notifies a running
macropad-bridge to reload.

Requires the `webui` extra: `uv sync --extra webui`. Binds to
127.0.0.1 only -- this is a local tool, not meant to be exposed.
"""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

from . import config as macropad_config

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096  # avoid rewrapping long comments/strings on save

VERSIONS_DIR = macropad_config.REPO_ROOT / "config-versions"
APPLIED_MARKER = VERSIONS_DIR / ".applied"
STATIC_DIR = Path(__file__).resolve().parent / "webui_static"

EDITABLE_FILES = ["colors.yaml", "keymap.yaml", "bridge.yaml"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

app = Flask(__name__, static_folder=None)


# --- version storage -------------------------------------------------------


def _version_dir(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"invalid version name: {name!r}")
    return VERSIONS_DIR / name


def _ensure_seeded() -> None:
    """Creates config-versions/default/ from the live config/ on first
    run, so there's always at least one version to edit."""
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = [p for p in VERSIONS_DIR.iterdir() if p.is_dir()]
    if existing:
        return
    default_dir = _version_dir("default")
    default_dir.mkdir(parents=True, exist_ok=True)
    for filename in EDITABLE_FILES:
        src = macropad_config.CONFIG_DIR / filename
        if src.exists():
            shutil.copy2(src, default_dir / filename)
    APPLIED_MARKER.write_text("default")


def list_versions() -> list[dict]:
    _ensure_seeded()
    applied = _applied_version()
    versions = []
    for entry in sorted(VERSIONS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        versions.append({"name": entry.name, "applied": entry.name == applied})
    return versions


def _applied_version() -> str | None:
    if not APPLIED_MARKER.exists():
        return None
    return APPLIED_MARKER.read_text().strip() or None


def create_version(name: str, clone_from: str | None) -> None:
    dest = _version_dir(name)
    if dest.exists():
        raise ValueError(f"version {name!r} already exists")
    if clone_from:
        src = _version_dir(clone_from)
        if not src.exists():
            raise ValueError(f"source version {clone_from!r} does not exist")
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True)


def delete_version(name: str) -> None:
    dest = _version_dir(name)
    if not dest.exists():
        raise ValueError(f"version {name!r} does not exist")
    shutil.rmtree(dest)
    if _applied_version() == name:
        APPLIED_MARKER.unlink(missing_ok=True)


def rename_version(name: str, new_name: str) -> None:
    src = _version_dir(name)
    dest = _version_dir(new_name)
    if not src.exists():
        raise ValueError(f"version {name!r} does not exist")
    if dest.exists():
        raise ValueError(f"version {new_name!r} already exists")
    src.rename(dest)
    if _applied_version() == name:
        APPLIED_MARKER.write_text(new_name)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return _yaml.load(f) or {}


def _coerce_key(new_key, existing_keys):
    """JSON object keys are always strings (e.g. "8"), but keymap.yaml's
    `keys:`/`actions:` use bare integer keys (8:). Without this, every
    merge would see string keys as "not in existing", delete the
    original int-keyed entries (and their comments) and re-add
    string-keyed duplicates. Matches new_key against whichever key type
    is already on disk. For a genuinely new digit-string key (e.g.
    adding session key 3 for the first time, which has no existing
    entry to match against), still coerces to int rather than leaving
    it a raw string -- `keys:`/`actions:` are always int-keyed in this
    schema, and code elsewhere (e.g. bridge.py's _send_selected_label)
    does plain int lookups without a string fallback, so a stray
    string key would silently never match."""
    if new_key in existing_keys:
        return new_key
    if isinstance(new_key, str) and new_key.lstrip("-").isdigit():
        return int(new_key)
    return new_key


def _merge_into(existing, new):
    """Recursively updates `existing` (a ruamel CommentedMap/Seq loaded
    from a version file, or a fresh dict for brand-new keys) in place
    with values from `new` (a plain dict from the frontend's JSON body),
    so comments/formatting attached to keys that already existed survive
    the round-trip. New keys are inserted as plain values -- ruamel is
    still able to dump those, they just won't carry a comment (there
    wasn't one to preserve in the first place)."""
    if isinstance(new, dict):
        if not isinstance(existing, dict):
            return new
        existing_keys = set(existing.keys())
        new_coerced = {_coerce_key(k, existing_keys): v for k, v in new.items()}
        for k, v in new_coerced.items():
            if k in existing:
                existing[k] = _merge_into(existing[k], v)
            else:
                existing[k] = v
        # Drop keys the frontend removed (e.g. deleting a color state).
        for k in list(existing.keys()):
            if k not in new_coerced:
                del existing[k]
        return existing
    if isinstance(new, list):
        # Preserve flow style (e.g. `color: [255, 0, 0]`) when replacing
        # a list that was already written that way -- otherwise a plain
        # Python list from the frontend's JSON dumps as multi-line block
        # style, which is a needless reformat of files meant to stay
        # human-readable.
        was_flow = isinstance(existing, CommentedSeq) and existing.fa.flow_style()
        if was_flow:
            seq = CommentedSeq(new)
            seq.fa.set_flow_style()
            return seq
        return new
    return new


def get_version_config(name: str) -> dict:
    version_dir = _version_dir(name)
    if not version_dir.exists():
        raise ValueError(f"version {name!r} does not exist")
    return {
        "colors": _load_yaml(version_dir / "colors.yaml"),
        "keymap": _load_yaml(version_dir / "keymap.yaml"),
        "bridge": _load_yaml(version_dir / "bridge.yaml"),
    }


def put_version_config(name: str, data: dict) -> None:
    """Merges `data` into each YAML file's existing content in place
    (see _merge_into) rather than a full re-dump, so hand-written
    comments in colors.yaml/keymap.yaml/bridge.yaml survive edits made
    through the UI."""
    version_dir = _version_dir(name)
    if not version_dir.exists():
        raise ValueError(f"version {name!r} does not exist")
    mapping = {"colors": "colors.yaml", "keymap": "keymap.yaml", "bridge": "bridge.yaml"}
    for key, filename in mapping.items():
        if key not in data:
            continue
        path = version_dir / filename
        existing = _load_yaml(path)
        merged = _merge_into(existing, data[key])
        with open(path, "w") as f:
            _yaml.dump(merged, f)


def apply_version(name: str) -> dict:
    version_dir = _version_dir(name)
    if not version_dir.exists():
        raise ValueError(f"version {name!r} does not exist")
    for filename in EDITABLE_FILES:
        src = version_dir / filename
        if src.exists():
            shutil.copy2(src, macropad_config.CONFIG_DIR / filename)
    APPLIED_MARKER.write_text(name)
    notified = _notify_bridge()
    return {"notified_bridge": notified}


def _notify_bridge() -> bool:
    """Best-effort ping to a running macropad-bridge to reload its
    cached voice/encoder settings (see Bridge._reload_config in
    bridge.py) -- colors.yaml/keymap.yaml don't need this since they're
    already read fresh on every access (see macropad/config.py). Returns
    False (never raises) if the bridge isn't running."""
    settings = macropad_config.load_bridge_settings()
    host = settings.get("host", "127.0.0.1")
    port = settings.get("port", 9999)
    try:
        with socket.create_connection((host, port), timeout=0.5) as sock:
            sock.sendall((json.dumps({"reload_config": True}) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def _bridge_running() -> bool:
    settings = macropad_config.load_bridge_settings()
    host = settings.get("host", "127.0.0.1")
    port = settings.get("port", 9999)
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


# --- routes ------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/versions")
def api_list_versions():
    return jsonify({"versions": list_versions()})


@app.post("/api/versions")
def api_create_version():
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    clone_from = body.get("clone_from")
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        create_version(name, clone_from)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.delete("/api/versions/<name>")
def api_delete_version(name: str):
    try:
        delete_version(name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.post("/api/versions/<name>/rename")
def api_rename_version(name: str):
    body = request.get_json(force=True) or {}
    new_name = body.get("new_name", "").strip()
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
    try:
        rename_version(name, new_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.get("/api/versions/<name>/config")
def api_get_config(name: str):
    try:
        return jsonify(get_version_config(name))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.put("/api/versions/<name>/config")
def api_put_config(name: str):
    body: dict[str, Any] = request.get_json(force=True) or {}
    try:
        put_version_config(name, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.post("/api/versions/<name>/apply")
def api_apply_version(name: str):
    try:
        result = apply_version(name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@app.get("/api/state")
def api_state():
    return jsonify({"bridge_running": _bridge_running()})


def main() -> None:
    try:
        import flask  # noqa: F401
    except ImportError:
        print("[macropad-webui] the 'flask' package isn't installed -- run "
              "`uv sync --extra webui` first.", flush=True)
        raise SystemExit(1)

    _ensure_seeded()
    print(f"[macropad-webui] serving on http://{DEFAULT_HOST}:{DEFAULT_PORT}", flush=True)
    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT, debug=False)


if __name__ == "__main__":
    main()
