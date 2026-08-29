"""Load API keys from the environment (optionally via .env)."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_dotenv_loaded = False


def load_dotenv_once() -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    env_path = REPO_ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                os.environ.setdefault(key, value)


def api_key(env_var: str) -> str | None:
    load_dotenv_once()
    value = os.environ.get(env_var)
    if value:
        return value.strip()
    return None
