"""Per-user brand profile for multi-tenant prompt injection."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.config import ROOT
from schemas.models import BrandProfile

_lock = threading.Lock()
_USERS_DIR = ROOT / "projects" / ".users"

# Style-guide keys that brand fields map into (for Veo / Gemini)
_STYLE_KEYS = (
    "visual_tone",
    "mood",
    "color_palette",
    "camera_style",
    "broll_casting",
    "avoid",
    "filler_clip",
    "format",
)


def _safe_user_id(user_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or ""))[:64] or "unknown"


def user_brand_path(user_id: str) -> Path:
    return _USERS_DIR / _safe_user_id(user_id) / "brand.json"


def empty_brand() -> BrandProfile:
    return BrandProfile()


def load_user_brand(user_id: str | None) -> BrandProfile:
    if not user_id or user_id == "local":
        return empty_brand()
    path = user_brand_path(user_id)
    if not path.exists():
        return empty_brand()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return BrandProfile.model_validate(data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return empty_brand()


def save_user_brand(user_id: str, brand: BrandProfile) -> BrandProfile:
    if not user_id or user_id == "local":
        raise ValueError("Cannot save brand without a real user id")
    path = user_brand_path(user_id)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = brand.model_dump(mode="json", exclude_none=False)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    return brand


def brand_as_style_dict(brand: BrandProfile) -> dict[str, str]:
    """Map BrandProfile → style_guide.json keys (skip empty)."""
    out: dict[str, str] = {}
    data = brand.model_dump()
    setting = (data.get("setting") or data.get("visual_world") or "").strip()
    if setting:
        # Prefer setting as visual_tone when visual_tone empty; also store as visual_world key
        out["visual_world"] = setting
        if not (data.get("visual_tone") or "").strip():
            out["visual_tone"] = setting
    for key in _STYLE_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    niche = (data.get("niche") or "").strip()
    audience = (data.get("audience") or "").strip()
    if niche:
        out["niche"] = niche
    if audience:
        out["audience"] = audience
    return out


def brand_context_text(brand: BrandProfile) -> str:
    """Readable brand block for editing-plan / editor prompts."""
    parts: list[str] = []
    if brand.niche:
        parts.append(f"- Topic domain: {brand.niche.strip()}")
    if brand.audience:
        parts.append(f"- Target audience: {brand.audience.strip()}")
    world = (brand.setting or brand.visual_world or brand.visual_tone or "").strip()
    if world:
        parts.append(f"- Visual world / setting: {world}")
    if brand.broll_casting:
        parts.append(f"- B-roll casting: {brand.broll_casting.strip()}")
    if brand.mood:
        parts.append(f"- Mood: {brand.mood.strip()}")
    if brand.color_palette:
        parts.append(f"- Color palette: {brand.color_palette.strip()}")
    if brand.camera_style:
        parts.append(f"- Camera style: {brand.camera_style.strip()}")
    if brand.avoid:
        parts.append(f"- Avoid: {brand.avoid.strip()}")
    if not parts:
        return (
            "- No brand profile set — use literal, filmable B-roll grounded in the spoken line; "
            "keep casting consistent within the reel; avoid stock clichés."
        )
    return "\n".join(parts)


def owner_id_from_project(project_dir: Path | None) -> str | None:
    if not project_dir:
        return None
    try:
        from core.project_store import load_manifest

        return load_manifest(project_dir).owner_id
    except Exception:
        return None


def load_merged_style_guide(
    project_dir: Path | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Merge order (later wins):
      1) global style_guide.json
      2) per-user brand
      3) optional project style_guide.json
    """
    guide: dict[str, Any] = {}
    global_path = ROOT / "style_guide.json"
    if global_path.exists():
        try:
            guide = json.loads(global_path.read_text(encoding="utf-8"))
            if not isinstance(guide, dict):
                guide = {}
        except (OSError, json.JSONDecodeError):
            guide = {}

    uid = user_id or owner_id_from_project(project_dir)
    brand_dict = brand_as_style_dict(load_user_brand(uid))
    if brand_dict:
        guide.update(brand_dict)

    if project_dir:
        project_path = project_dir / "style_guide.json"
        if project_path.exists():
            try:
                overlay = json.loads(project_path.read_text(encoding="utf-8"))
                if isinstance(overlay, dict):
                    guide.update(overlay)
            except (OSError, json.JSONDecodeError):
                pass
    return guide


def style_guide_text(
    project_dir: Path | None = None,
    *,
    user_id: str | None = None,
) -> str:
    guide = load_merged_style_guide(project_dir, user_id=user_id)
    uid = user_id or owner_id_from_project(project_dir)
    brand = load_user_brand(uid)
    lines = ["## Style Guide"]
    for key, val in guide.items():
        if key in ("filler_clip", "niche", "audience", "visual_world"):
            continue
        if val:
            lines.append(f"- {key.replace('_', ' ').title()}: {val}")
    brand_block = brand_context_text(brand)
    lines.append("")
    lines.append("## Brand & scene (obey for B-roll casting / setting)")
    lines.append(brand_block)
    return "\n".join(lines)
