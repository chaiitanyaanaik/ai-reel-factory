"""Central env loading and feature flags."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def environment_name() -> str:
    """Environment name (host may set ENVIRONMENT before dotenv runs)."""
    return (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "development").strip().lower()


def is_production_env() -> bool:
    return environment_name() in ("production", "prod")


def load_env(*, force: bool = False) -> None:
    """
    Load local dotenv files into os.environ.

    Best practice:
    - Process / host / CI env always wins (dotenv never overwrites existing keys).
    - Local: `.env` fills gaps; optional `.env.local` may override for personal tweaks.
    - Production: same fill-gaps rule — inject secrets on the host; do not commit them.

    Restart the API after editing `.env` (values already in the process are not replaced).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    # Host/CI first: never clobber variables already set in the process.
    load_dotenv(ROOT / ".env", override=False)

    if not is_production_env():
        # Personal overrides (gitignored) — allowed to win over `.env` only.
        load_dotenv(ROOT / ".env.local", override=True)


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
