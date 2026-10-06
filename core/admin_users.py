"""Admin helpers: ADMIN_EMAILS gate + aggregated user list for the portal."""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import HTTPException, status

from core.auth import CurrentUser
from core.config import load_env
from core.entitlements import (
    find_user_id_by_email,
    list_entitlements,
    load_entitlement,
    resolve_project_quota,
    save_entitlement,
)
from core.usage_limits import lifetime_projects_created
from core.user_profiles import list_profiles


def _csv_env(name: str, *, lower: bool = False) -> set[str]:
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


def admin_emails() -> set[str]:
    return _csv_env("ADMIN_EMAILS", lower=True)


def admin_user_ids() -> set[str]:
    return _csv_env("ADMIN_USER_IDS")


def is_admin(user: CurrentUser) -> bool:
    """
    Admin only if listed in ADMIN_USER_IDS or ADMIN_EMAILS.

    Security: email is taken ONLY from the verified Clerk/dev JWT — never from
    client-synced profile.json (that would let any signed-in user spoof an admin email).
    For ADMIN_EMAILS to work with Clerk, add email to the session token:
      Sessions → Customize session token → {"email":"{{user.primary_email_address}}"}
    Or use ADMIN_USER_IDS=user_xxx (always present as JWT `sub`).
    """
    if not user or user.id == "local":
        return False

    ids = admin_user_ids()
    if user.id in ids or user.id.lower() in {x.lower() for x in ids}:
        return True

    emails = admin_emails()
    if not emails:
        return False

    jwt_email = (user.email or "").strip().lower()
    return bool(jwt_email and jwt_email in emails)


def require_admin(user: CurrentUser) -> CurrentUser:
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return user


def _owner_ids_from_projects() -> set[str]:
    from core.project_store import PROJECTS_DIR, ensure_manifest

    out: set[str] = set()
    if not PROJECTS_DIR.is_dir():
        return out
    for path in PROJECTS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            m = ensure_manifest(path)
        except Exception:
            continue
        if m.owner_id:
            out.add(m.owner_id)
    return out


def list_admin_users() -> list[dict[str, Any]]:
    """Merge profiles, entitlements, and project owners for the admin table."""
    by_key: dict[str, dict[str, Any]] = {}

    def key_for(user_id: Optional[str], email: Optional[str]) -> str:
        if user_id:
            return f"id:{user_id}"
        if email:
            return f"email:{(email or '').strip().lower()}"
        return "unknown"

    def upsert(
        *,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        last_seen_at: Optional[str] = None,
    ) -> dict[str, Any]:
        k = key_for(user_id, email)
        row = by_key.get(k) or {
            "user_id": user_id,
            "email": (email or "").strip().lower() or None,
            "name": name,
            "last_seen_at": last_seen_at,
            "pending": not bool(user_id),
        }
        if user_id:
            row["user_id"] = user_id
            row["pending"] = False
        if email:
            row["email"] = email.strip().lower()
        if name and not row.get("name"):
            row["name"] = name
        if last_seen_at:
            row["last_seen_at"] = last_seen_at
        by_key[k] = row
        # Merge email-only into id key when both appear
        if user_id and email:
            ek = key_for(None, email)
            if ek in by_key and ek != k:
                old = by_key.pop(ek)
                if old.get("name") and not row.get("name"):
                    row["name"] = old["name"]
        return row

    for p in list_profiles():
        upsert(
            user_id=str(p.get("user_id") or "") or None,
            email=p.get("email"),
            name=p.get("name"),
            last_seen_at=p.get("last_seen_at"),
        )

    for ent in list_entitlements():
        upsert(user_id=ent.get("user_id"), email=ent.get("email"))

    for oid in _owner_ids_from_projects():
        upsert(user_id=oid)

    rows: list[dict[str, Any]] = []
    for row in by_key.values():
        uid = row.get("user_id")
        email = row.get("email")
        ent = load_entitlement(user_id=uid, email=email)
        quota = resolve_project_quota(uid, email)
        used = lifetime_projects_created(uid) if uid else 0
        rows.append(
            {
                "user_id": uid,
                "email": email,
                "name": row.get("name"),
                "last_seen_at": row.get("last_seen_at"),
                "pending": bool(row.get("pending")),
                "plan": quota.plan,
                "project_limit": quota.limit,
                "unlimited": quota.unlimited,
                "projects_used": used,
                "entitlement": ent,
                "source": quota.source,
            }
        )

    rows.sort(
        key=lambda r: (
            0 if r.get("last_seen_at") else 1,
            r.get("last_seen_at") or "",
            r.get("email") or "",
            r.get("user_id") or "",
        ),
        reverse=True,
    )
    return rows


def patch_user_entitlement(
    *,
    user_id: str,
    plan: str,
    project_limit: Optional[int],
    notes: str = "",
    updated_by: Optional[str] = None,
) -> dict[str, Any]:
    if plan not in ("free", "paid"):
        raise HTTPException(status_code=400, detail="plan must be free or paid")
    from core.user_profiles import load_profile

    profile = load_profile(user_id)
    email = (profile or {}).get("email") if profile else None
    # Also try existing entitlement email
    existing = load_entitlement(user_id=user_id)
    if not email and existing:
        email = existing.get("email")
    ent = save_entitlement(
        user_id=user_id,
        email=email,
        plan=plan,  # type: ignore[arg-type]
        project_limit=project_limit,
        notes=notes,
        updated_by=updated_by,
    )
    return ent


def upsert_by_email(
    *,
    email: str,
    plan: str,
    project_limit: Optional[int],
    notes: str = "",
    updated_by: Optional[str] = None,
) -> dict[str, Any]:
    if plan not in ("free", "paid"):
        raise HTTPException(status_code=400, detail="plan must be free or paid")
    email_n = email.strip().lower()
    if "@" not in email_n:
        raise HTTPException(status_code=400, detail="Valid email required")
    user_id = find_user_id_by_email(email_n)
    return save_entitlement(
        user_id=user_id,
        email=email_n,
        plan=plan,  # type: ignore[arg-type]
        project_limit=project_limit,
        notes=notes,
        updated_by=updated_by,
    )
