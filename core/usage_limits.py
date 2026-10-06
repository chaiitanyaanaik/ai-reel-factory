"""Per-user daily usage caps + free-tier lifetime project quota."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import HTTPException, status

from core.config import ROOT, env_int, load_env

Action = Literal["project_create", "job", "broll_edit"]

_lock = threading.Lock()
_USAGE_DIR = ROOT / "projects" / ".usage"


def _today() -> str:
    return date.today().isoformat()


def _csv_tokens(name: str, *, lower: bool = False) -> set[str]:
    load_env()
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        out.add(token.lower() if lower else token)
    return out


def free_project_limit() -> int:
    """
    Global default max projects for free users (lifetime; deleting does not reset).
    0 = unlimited for everyone. Default 3.
    """
    return env_int("FREE_PROJECT_LIMIT", 3)


def is_unlimited_user(user_id: str, email: Optional[str] = None) -> bool:
    """True if effective quota is unlimited (entitlement, env allowlist, or default off)."""
    from core.entitlements import resolve_project_quota

    if not user_id:
        return False
    return resolve_project_quota(user_id, email).unlimited


def _limits() -> dict[Action, int]:
    load_env()
    return {
        "project_create": env_int("RATE_LIMIT_PROJECTS_PER_DAY", 20),
        "job": env_int("RATE_LIMIT_JOBS_PER_DAY", 30),
        "broll_edit": env_int("RATE_LIMIT_BROLL_EDITS_PER_DAY", 40),
    }


def _user_dir(user_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)[:64]
    return _USAGE_DIR / safe


def _path(user_id: str, day: str) -> Path:
    return _user_dir(user_id) / f"{day}.json"


def _lifetime_path(user_id: str) -> Path:
    return _user_dir(user_id) / "lifetime.json"


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


def _owned_project_count(user_id: str) -> int:
    if not user_id:
        return 0
    from core.project_store import list_projects

    return len(list_projects(owner_id=user_id))


def lifetime_projects_created(user_id: str) -> int:
    """
    Lifetime projects created by this user (never decreases on delete).
    If no counter file yet, seed from current owned count so existing users
    are not reset to zero.
    """
    if not user_id:
        return 0
    path = _lifetime_path(user_id)
    with _lock:
        data = _read(path)
        if "projects_created" in data:
            return int(data["projects_created"] or 0)
        seeded = _owned_project_count(user_id)
        _write(path, {"projects_created": seeded})
        return seeded


def record_project_created(user_id: str) -> int:
    """
    Increment lifetime free-tier counter after a successful create.
    If no counter exists yet, seed from current owned count (includes the new project).
    """
    if not user_id or user_id == "local":
        return 0
    path = _lifetime_path(user_id)
    with _lock:
        data = _read(path)
        if "projects_created" in data:
            current = int(data["projects_created"] or 0) + 1
        else:
            # Folder already exists; owned count includes this create.
            current = _owned_project_count(user_id)
        _write(path, {"projects_created": current})
        return current


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


def assert_free_project_quota(user_id: str, email: Optional[str] = None) -> None:
    """
    Enforce per-user lifetime project quota (entitlement / env / free default).
    Raises HTTP 402 when the allowance is used up.
    """
    if not user_id or user_id == "local":
        return
    from core.entitlements import resolve_project_quota

    quota = resolve_project_quota(user_id, email)
    if quota.unlimited or quota.limit is None:
        return
    used = lifetime_projects_created(user_id)
    if used >= quota.limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Your plan includes {quota.limit} project{'s' if quota.limit != 1 else ''}. "
                "Upgrade to paid for more projects."
            ),
        )


def usage_snapshot(user_id: str, email: Optional[str] = None) -> dict[str, object]:
    """Current day counts + configured limits + project quota."""
    from core.entitlements import resolve_project_quota

    limits = _limits()
    day = _today()
    counts = _read(_path(user_id, day)) if user_id else {}
    quota = (
        resolve_project_quota(user_id, email)
        if user_id
        else None
    )
    projects_used = lifetime_projects_created(user_id) if user_id else 0
    unlimited = bool(quota and quota.unlimited)
    return {
        "day": day,
        "limits": limits,
        "used": {k: int(counts.get(k, 0) or 0) for k in limits},
        "projects": {
            "used": projects_used,
            "limit": None if unlimited else (quota.limit if quota else free_project_limit()),
            "unlimited": unlimited,
            "plan": (quota.plan if quota else "free"),
        },
    }
