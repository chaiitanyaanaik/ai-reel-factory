"""Auth helpers: JWT for multi-user Phase 1 (dev login + optional Clerk)."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import load_env

_bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger("ai_reel_factory.auth")

_WEAK_SECRETS = frozenset(
    {
        "",
        "reelkut-dev-secret-change-me",
        "reelkut-dev-secret-change-me-32b",
        "changeme",
        "secret",
    }
)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    name: Optional[str] = None


def environment_name() -> str:
    load_env()
    return (os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "development").strip().lower()


def is_production() -> bool:
    return environment_name() in ("production", "prod")


def auth_mode() -> str:
    load_env()
    return (os.environ.get("AUTH_MODE") or "dev").strip().lower()


def auth_required() -> bool:
    """When False, API stays open (legacy local). When True, Bearer required."""
    load_env()
    mode = auth_mode()
    if mode in ("off", "none", "disabled"):
        return False
    raw = os.environ.get("AUTH_REQUIRED", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    # Default: require auth for dev/clerk modes
    return mode in ("dev", "clerk")


def cookie_secure() -> bool:
    """HTTPS-only cookies in production (or when COOKIE_SECURE=1)."""
    load_env()
    raw = (os.environ.get("COOKIE_SECURE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return is_production()


def _auth_secret() -> str:
    load_env()
    secret = (os.environ.get("AUTH_SECRET") or "").strip()
    if not secret:
        if is_production() or auth_mode() == "clerk":
            raise RuntimeError(
                "AUTH_SECRET is required in production / AUTH_MODE=clerk. "
                "Set a long random value (≥32 chars)."
            )
        secret = "reelkut-dev-secret-change-me"
    return secret


def validate_auth_config() -> None:
    """
    Fail closed on unsafe public deploys. Call at API startup.
    Set ENVIRONMENT=production on shared hosts.
    """
    load_env()
    mode = auth_mode()
    env = environment_name()
    secret = (os.environ.get("AUTH_SECRET") or "").strip()

    if is_production():
        if mode == "dev":
            raise RuntimeError(
                "AUTH_MODE=dev is not allowed when ENVIRONMENT=production. "
                "Use AUTH_MODE=clerk (or another real identity provider)."
            )
        if mode in ("off", "none", "disabled"):
            raise RuntimeError(
                "AUTH_MODE=off is not allowed when ENVIRONMENT=production."
            )
        if secret.lower() in _WEAK_SECRETS or len(secret) < 32:
            raise RuntimeError(
                "AUTH_SECRET must be set to a strong unique value (≥32 chars) in production."
            )
        if mode == "clerk" and not (os.environ.get("CLERK_ISSUER") or "").strip():
            raise RuntimeError("CLERK_ISSUER is required when AUTH_MODE=clerk in production.")

    if secret.lower() in _WEAK_SECRETS and mode == "dev":
        logger.warning(
            "AUTH_SECRET is weak/default — fine for local only; "
            "set ENVIRONMENT=production + AUTH_MODE=clerk before a public rollout."
        )

    # Never log secret values — presence only
    google = bool((os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip())
    pexels = bool((os.environ.get("PEXELS_API_KEY") or "").strip())
    logger.info(
        "Auth config: environment=%s auth_mode=%s auth_required=%s "
        "GOOGLE_API_KEY_set=%s PEXELS_API_KEY_set=%s cookie_secure=%s",
        env,
        mode,
        auth_required(),
        google,
        pexels,
        cookie_secure(),
    )


def _user_id_from_email(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"usr_{digest[:16]}"


def issue_dev_token(*, email: str, name: Optional[str] = None) -> tuple[str, CurrentUser]:
    """Issue HS256 JWT for AUTH_MODE=dev (no password — email is the identity)."""
    try:
        import jwt
    except ImportError as e:
        raise RuntimeError("Install PyJWT: pip install PyJWT") from e

    email_n = email.strip().lower()
    if "@" not in email_n:
        raise ValueError("email must look like an email address")
    user = CurrentUser(
        id=_user_id_from_email(email_n),
        email=email_n,
        name=(name or email_n.split("@")[0]).strip() or None,
    )
    now = int(time.time())
    ttl = int(os.environ.get("AUTH_TOKEN_TTL_SECONDS", "604800") or 604800)  # 7d
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "iat": now,
        "exp": now + max(3600, ttl),
        "iss": "reelkut-dev",
    }
    token = jwt.encode(payload, _auth_secret(), algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, user


def issue_media_token(user: CurrentUser) -> tuple[str, int]:
    """
    Short-lived HS256 token for <video>/<img> URLs (query param).
    Prefer httponly cookie when same-origin; media tokens reduce session JWT leakage.
    """
    try:
        import jwt
    except ImportError as e:
        raise RuntimeError("Install PyJWT: pip install PyJWT") from e

    load_env()
    ttl = int(os.environ.get("MEDIA_TOKEN_TTL_SECONDS", "3600") or 3600)
    ttl = max(300, min(ttl, 86400))
    now = int(time.time())
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "iat": now,
        "exp": now + ttl,
        "iss": "reelkut-media",
        "typ": "media",
    }
    token = jwt.encode(payload, _auth_secret(), algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, ttl


def _decode_dev_token(token: str) -> CurrentUser:
    import jwt

    try:
        payload = jwt.decode(
            token,
            _auth_secret(),
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    iss = str(payload.get("iss") or "")
    if iss not in ("reelkut-dev", "reelkut-media"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token issuer",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(
        id=str(payload["sub"]),
        email=str(payload.get("email") or ""),
        name=payload.get("name"),
    )


def _decode_clerk_token(token: str) -> CurrentUser:
    """Verify Clerk session JWT via JWKS (AUTH_MODE=clerk)."""
    load_env()
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as e:
        raise RuntimeError("Install PyJWT: pip install PyJWT") from e

    issuer = (os.environ.get("CLERK_ISSUER") or "").strip().rstrip("/")
    if not issuer:
        raise HTTPException(
            status_code=500,
            detail="CLERK_ISSUER not configured",
        )
    jwks_url = (os.environ.get("CLERK_JWKS_URL") or f"{issuer}/.well-known/jwks.json").strip()
    audience = (os.environ.get("CLERK_AUDIENCE") or "").strip() or None
    try:
        client = PyJWKClient(jwks_url)
        key = client.get_signing_key_from_jwt(token)
        kwargs = {
            "algorithms": ["RS256"],
            "issuer": issuer,
            "options": {"require": ["exp", "sub"]},
        }
        if audience:
            kwargs["audience"] = audience
        payload = jwt.decode(token, key.key, **kwargs)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Clerk token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    email = (
        payload.get("email")
        or payload.get("primary_email_address")
        or ""
    )
    return CurrentUser(
        id=str(payload["sub"]),
        email=str(email),
        name=payload.get("name") or payload.get("full_name"),
    )


def decode_access_token(token: str) -> CurrentUser:
    mode = auth_mode()
    # Short-lived media tokens are always HS256 (even under Clerk) so <video> URLs
    # do not embed the long-lived Clerk session JWT.
    try:
        import jwt as _jwt

        unverified = _jwt.decode(token, options={"verify_signature": False})
        if unverified.get("iss") == "reelkut-media" or unverified.get("typ") == "media":
            return _decode_dev_token(token)
    except Exception:
        pass

    if mode == "clerk":
        return _decode_clerk_token(token)
    return _decode_dev_token(token)


def _extract_token(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials],
    access_token: Optional[str],
) -> Optional[str]:
    # Prefer Authorization, then httponly cookie, then query (last resort — logs/Referer).
    if creds and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials
    cookie = request.cookies.get("reelkut_token")
    if cookie:
        return cookie
    if access_token:
        return access_token
    return None


def get_current_user_optional(
    request: Request,
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)] = None,
    access_token: Annotated[Optional[str], Query(alias="access_token")] = None,
) -> Optional[CurrentUser]:
    token = _extract_token(request, creds, access_token)
    if not token:
        return None
    return decode_access_token(token)


def get_current_user(
    user: Annotated[Optional[CurrentUser], Depends(get_current_user_optional)],
) -> CurrentUser:
    if user is not None:
        return user
    if not auth_required():
        # Legacy open mode — synthetic local user (projects still get owner when set).
        return CurrentUser(id="local", email="local@localhost", name="Local")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_project_owner(manifest_owner_id: Optional[str], user: CurrentUser) -> None:
    """403 if project belongs to someone else. Legacy ownerless projects: allow only in AUTH_MODE=off."""
    if not auth_required():
        return
    if manifest_owner_id is None:
        # Orphan / pre-auth project — hide unless LEGACY_PUBLIC_PROJECTS=1
        load_env()
        if (os.environ.get("LEGACY_PUBLIC_PROJECTS") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            return
        raise HTTPException(status_code=404, detail="Project not found")
    if manifest_owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
