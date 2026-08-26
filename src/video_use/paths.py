"""Config paths and API-key resolution for video-use."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base else Path.home() / ".config"
    return root / "video-use"


def _read_env_file(path: Path, key: str) -> str:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def resolve_api_key(key: str = "ELEVENLABS_API_KEY") -> tuple[str, str]:
    """Return (value, source). Source is 'env', a file path, or '' if not found.

    Order: environment variable, .env in the current directory, then
    <config_dir>/.env, then a repo-root .env for git-clone installs.
    """
    v = os.environ.get(key, "")
    if v:
        return v, "env"
    candidates = [
        Path(".env"),
        config_dir() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            v = _read_env_file(candidate, key)
            if v:
                return v, str(candidate)
    return "", ""
