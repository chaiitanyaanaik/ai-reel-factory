"""Per-user daily usage caps to protect shared provider API keys."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, status

from core.config import ROOT, env_int, load_env

Action = Literal["project_create", "job", "broll_edit"]

_lock = threading.Lock()
_USAGE_DIR = ROOT / "projects" / ".usage"


def _today() -> str:
    return date.today().isoformat()


def _limits() -> dict[Action, int]:
    load_env()
    return {
        "project_create": env_int("RATE_LIMIT_PROJECTS_PER_DAY", 20),
        "job": env_int("RATE_LIMIT_JOBS_PER_DAY", 30),
        "broll_edit": env_int("RATE_LIMIT_BROLL_EDITS_PER_DAY", 40),
    }


def _path(user_id: str, day: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)[:64]
    return _USAGE_DIR / safe / f"{day}.json"


def _read(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items() if not str(k).startswith("_")}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {}


def _write(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **counts,
        "_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def assert_under_limit(user_id: str, action: Action) -> None:
    """Raise 429 if already at/over daily cap (does not increment)."""
    if not user_id or user_id == "local":
        return
    limits = _limits()
    limit = limits.get(action, 0)
    if limit <= 0:
        return
    day = _today()
    path = _path(user_id, day)
    with _lock:
        counts = _read(path)
        current = int(counts.get(action, 0) or 0)
        if current >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Daily {action.replace('_', ' ')} limit reached ({limit}/day). "
                    "Try again tomorrow or contact the operator."
                ),
            )


def check_and_increment(user_id: str, action: Action) -> None:
    """
    Enforce daily per-user caps. Limit 0 disables that action's check.
    Raises HTTP 429 when exceeded.
    """
    if not user_id or user_id == "local":
        return
    limits = _limits()
    limit = limits.get(action, 0)
    if limit <= 0:
        return

    day = _today()
    path = _path(user_id, day)
    with _lock:
        counts = _read(path)
        current = int(counts.get(action, 0) or 0)
        if current >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Daily {action.replace('_', ' ')} limit reached ({limit}/day). "
                    "Try again tomorrow or contact the operator."
                ),
            )
        counts[action] = current + 1
        _write(path, counts)


def usage_snapshot(user_id: str) -> dict[str, object]:
    """Current day counts + configured limits."""
    limits = _limits()
    day = _today()
    counts = _read(_path(user_id, day)) if user_id else {}
    return {
        "day": day,
        "limits": limits,
        "used": {k: int(counts.get(k, 0) or 0) for k in limits},
    }
