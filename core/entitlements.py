"""Per-user plan / project_limit stored under projects/.users/."""
from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from core.config import env_int, load_env
from core.user_profiles import (
    PENDING_EMAIL_DIR,
    USERS_DIR,
    pending_email_dir,
    user_dir,
)

Plan = Literal["free", "paid"]

_lock = threading.Lock()


@dataclass
class EffectiveQuota:
    plan: Plan
    limit: Optional[int]  # None = unlimited
    unlimited: bool
    source: str  # entitlement | env | default


def free_project_limit_default() -> int:
    return env_int("FREE_PROJECT_LIMIT", 3)


def entitlement_path(user_id: str) -> Path:
    return user_dir(user_id) / "entitlement.json"


def pending_entitlement_path(email: str) -> Path:
    return pending_email_dir(email) / "entitlement.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _normalize_entitlement(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not data:
        return None
    plan = data.get("plan") or "free"
    if plan not in ("free", "paid"):
        plan = "free"
    raw_limit = data.get("project_limit", None)
    limit: Optional[int]
    if raw_limit is None or raw_limit == "":
        limit = None
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit < 0:
            limit = None
    return {
        "user_id": data.get("user_id"),
        "email": (data.get("email") or "").strip().lower() or None,
        "plan": plan,
        "project_limit": limit,
        "notes": data.get("notes") or "",
        "updated_at": data.get("updated_at"),
        "updated_by": data.get("updated_by"),
    }


def load_entitlement(
    user_id: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if user_id and user_id != "local":
        ent = _normalize_entitlement(_read_json(entitlement_path(user_id)))
        if ent:
            return ent
    email_n = (email or "").strip().lower()
    if email_n:
        ent = _normalize_entitlement(_read_json(pending_entitlement_path(email_n)))
        if ent:
            return ent
    return None


def save_entitlement(
    *,
    user_id: Optional[str],
    email: Optional[str],
    plan: Plan,
    project_limit: Optional[int],
    notes: str = "",
    updated_by: Optional[str] = None,
) -> dict[str, Any]:
    email_n = (email or "").strip().lower() or None
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "user_id": user_id,
        "email": email_n,
        "plan": plan,
        "project_limit": project_limit,
        "notes": notes or "",
        "updated_at": now,
        "updated_by": updated_by,
    }
    with _lock:
        if user_id and user_id != "local":
            _write_json(entitlement_path(user_id), payload)
            # Drop pending email copy if present
            if email_n and pending_entitlement_path(email_n).exists():
                try:
                    shutil.rmtree(pending_email_dir(email_n), ignore_errors=True)
                except OSError:
                    pass
        elif email_n:
            _write_json(pending_entitlement_path(email_n), payload)
        else:
            raise ValueError("user_id or email required")
    return payload


def bind_pending_email_entitlement(*, user_id: str, email: str) -> Optional[dict[str, Any]]:
    """Move pending-by-email entitlement onto the user id folder after login."""
    email_n = (email or "").strip().lower()
    if not user_id or not email_n:
        return None
    pending_path = pending_entitlement_path(email_n)
    if not pending_path.exists():
        # Already have user entitlement — nothing to bind
        return load_entitlement(user_id=user_id)
    with _lock:
        data = _normalize_entitlement(_read_json(pending_path))
        if not data:
            return None
        # Don't overwrite a newer user-scoped entitlement
        existing = _normalize_entitlement(_read_json(entitlement_path(user_id)))
        if existing and existing.get("updated_at") and data.get("updated_at"):
            if str(existing["updated_at"]) >= str(data["updated_at"]):
                try:
                    shutil.rmtree(pending_email_dir(email_n), ignore_errors=True)
                except OSError:
                    pass
                return existing
        data["user_id"] = user_id
        data["email"] = email_n
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(entitlement_path(user_id), data)
        try:
            shutil.rmtree(pending_email_dir(email_n), ignore_errors=True)
        except OSError:
            pass
        return data


def _env_unlimited(user_id: str, email: Optional[str]) -> bool:
    import os

    load_env()

    def csv(name: str, *, lower: bool = False) -> set[str]:
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

    ids = csv("UNLIMITED_USER_IDS")
    if user_id in ids or user_id.lower() in {x.lower() for x in ids}:
        return True
    if email and email.strip().lower() in csv("UNLIMITED_USER_EMAILS", lower=True):
        return True
    return False


def resolve_project_quota(
    user_id: Optional[str],
    email: Optional[str] = None,
) -> EffectiveQuota:
    """
    Resolve effective lifetime project quota.
    entitlement → env allowlist → FREE_PROJECT_LIMIT default.
    """
    if user_id == "local":
        return EffectiveQuota(plan="paid", limit=None, unlimited=True, source="local")

    ent = load_entitlement(user_id=user_id, email=email)
    if ent:
        plan: Plan = "paid" if ent["plan"] == "paid" else "free"
        lim = ent.get("project_limit")
        if plan == "paid":
            if lim is None or lim <= 0:
                return EffectiveQuota(plan="paid", limit=None, unlimited=True, source="entitlement")
            return EffectiveQuota(plan="paid", limit=lim, unlimited=False, source="entitlement")
        # free
        if lim is None:
            default = free_project_limit_default()
            if default <= 0:
                return EffectiveQuota(plan="free", limit=None, unlimited=True, source="entitlement")
            return EffectiveQuota(plan="free", limit=default, unlimited=False, source="entitlement")
        if lim <= 0:
            return EffectiveQuota(plan="free", limit=None, unlimited=True, source="entitlement")
        return EffectiveQuota(plan="free", limit=lim, unlimited=False, source="entitlement")

    if user_id and _env_unlimited(user_id, email):
        return EffectiveQuota(plan="paid", limit=None, unlimited=True, source="env")

    default = free_project_limit_default()
    if default <= 0:
        return EffectiveQuota(plan="free", limit=None, unlimited=True, source="default")
    return EffectiveQuota(plan="free", limit=default, unlimited=False, source="default")


def list_entitlements() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if USERS_DIR.is_dir():
        for path in USERS_DIR.iterdir():
            if not path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
                continue
            ent = _normalize_entitlement(_read_json(path / "entitlement.json"))
            if ent:
                rows.append(ent)
    if PENDING_EMAIL_DIR.is_dir():
        for path in PENDING_EMAIL_DIR.iterdir():
            if not path.is_dir():
                continue
            ent = _normalize_entitlement(_read_json(path / "entitlement.json"))
            if ent:
                rows.append(ent)
    return rows


def find_user_id_by_email(email: str) -> Optional[str]:
    from core.user_profiles import list_profiles

    email_n = (email or "").strip().lower()
    if not email_n:
        return None
    for p in list_profiles():
        if (p.get("email") or "").strip().lower() == email_n:
            return str(p.get("user_id") or "") or None
    return None
