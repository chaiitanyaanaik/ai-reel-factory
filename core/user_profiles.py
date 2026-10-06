"""Per-user profile records under projects/.users/<id>/profile.json."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.config import ROOT

_lock = threading.Lock()
USERS_DIR = ROOT / "projects" / ".users"
PENDING_EMAIL_DIR = USERS_DIR / "_by_email"


def safe_user_id(user_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or ""))[:64] or "unknown"


def safe_email_key(email: str) -> str:
    e = (email or "").strip().lower()
    return "".join(c if c.isalnum() or c in "-_@" else "_" for c in e)[:128] or "unknown"


def user_dir(user_id: str) -> Path:
    return USERS_DIR / safe_user_id(user_id)


def profile_path(user_id: str) -> Path:
    return user_dir(user_id) / "profile.json"


def pending_email_dir(email: str) -> Path:
    return PENDING_EMAIL_DIR / safe_email_key(email)


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


def load_profile(user_id: str) -> Optional[dict[str, Any]]:
    if not user_id or user_id == "local":
        return None
    data = _read_json(profile_path(user_id))
    return data or None


def touch_user_profile(
    *,
    user_id: str,
    email: str,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert profile on authenticated hits; bind any pending email entitlement."""
    if not user_id or user_id == "local":
        return {}
    email_n = (email or "").strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        path = profile_path(user_id)
        prev = _read_json(path)
        payload = {
            "user_id": user_id,
            "email": email_n or prev.get("email") or "",
            "name": (name if name is not None else prev.get("name")),
            "created_at": prev.get("created_at") or now,
            "last_seen_at": now,
        }
        _write_json(path, payload)

    if email_n:
        from core.entitlements import bind_pending_email_entitlement

        bind_pending_email_entitlement(user_id=user_id, email=email_n)

    return payload


def list_profiles() -> list[dict[str, Any]]:
    if not USERS_DIR.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in USERS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("_") or path.name.startswith("."):
            continue
        data = _read_json(path / "profile.json")
        if data.get("user_id") or data.get("email"):
            rows.append(data)
    return rows
