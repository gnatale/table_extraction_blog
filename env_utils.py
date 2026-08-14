"""Shared .env loading for extraction scripts."""

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env_file(env_path: Path = DEFAULT_ENV_FILE) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (existing vars win)."""
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


def require_env(key: str, env_path: Path = DEFAULT_ENV_FILE) -> str:
    """Return an env var or raise RuntimeError with setup instructions."""
    value = os.getenv(key)
    if not value:
        raise RuntimeError(
            f"{key} is not set. Add it to {env_path} or export it in your shell."
        )
    return value
