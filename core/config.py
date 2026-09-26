"""Central env loading and feature flags."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(*, force: bool = False) -> None:
    """
    Load project-root .env into os.environ.

    Always re-reads .env with override so local config changes apply without
    restarting long-lived API workers (still fine for production if .env is static).
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=True)
    except ImportError:
        pass


def allow_placeholders() -> bool:
    load_env()
    return os.environ.get("ALLOW_PLACEHOLDERS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def truthy(name: str, default: str = "0") -> bool:
    load_env()
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    load_env()
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    load_env()
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
