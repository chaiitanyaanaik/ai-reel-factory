"""
Style recipes: named presets for automatic pacing/zoom (non-editor).

Default `talking_head` matches pre-recipe EditorRules + alternate-segment zoom
so existing projects and builds stay behavior-identical when recipe is unset/default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Sequence

from .config import truthy

RecipeName = Literal["talking_head", "tutorial", "story"]
IntensityName = Literal["safer", "balanced", "punchy"]
ZoomPolicy = Literal["alternate_segment", "dense_alternate", "sparse_presence"]

VALID_RECIPES: tuple[RecipeName, ...] = ("talking_head", "tutorial", "story")
VALID_INTENSITIES: tuple[IntensityName, ...] = ("safer", "balanced", "punchy")


@dataclass(frozen=True)
class RecipeConfig:
    name: RecipeName
    intensity: IntensityName
    # None = keep env / EditorRules defaults (talking_head path)
    max_broll_count: Optional[int] = None
    min_broll_seconds: Optional[float] = None
    max_broll_seconds: Optional[float] = None
    min_gap_between_brolls: Optional[float] = None
    min_hook_aroll_seconds: Optional[float] = None
    min_end_aroll_seconds: Optional[float] = None
    min_aroll_share: Optional[float] = None
    zoom_policy: ZoomPolicy = "alternate_segment"
    caption_pack: str = "default"
    sfx_enabled: bool = False
    craft_brief: str = ""


# Intensity is stored for later (Phase 4); Phase 1 always uses these balanced knobs.
_RECIPE_SPECS: dict[RecipeName, dict[str, Any]] = {
    "talking_head": {
        "zoom_policy": "alternate_segment",
        "caption_pack": "default",
        "sfx_enabled": False,
        "craft_brief": (
            "Recipe: talking_head — face-forward tips. Prefer light cutaways; "
            "keep the speaker on screen most of the time."
        ),
    },
    "tutorial": {
        "min_gap_between_brolls": 2.8,
        "min_aroll_share": 0.50,
        "zoom_policy": "dense_alternate",
        "caption_pack": "default",
        "sfx_enabled": False,
        "craft_brief": (
            "Recipe: tutorial — teach/clarify. Prefer cutaways on steps and concrete nouns; "
            "slightly denser B-roll than a talking-head tip, still face-first hook and ending."
        ),
    },
    "story": {
        "max_broll_count": 3,
        "min_gap_between_brolls": 4.5,
        "min_aroll_share": 0.65,
        "zoom_policy": "sparse_presence",
        "caption_pack": "default",
        "sfx_enabled": False,
        "craft_brief": (
            "Recipe: story — narrative presence. Fewer cutaways; longer face time; "
            "use B-roll only for strong visual proof."
        ),
    },
}


def polish_recipes_enabled() -> bool:
    """Kill switch. Default on; talking_head still matches legacy behavior."""
    return truthy("POLISH_RECIPES", "1")


def normalize_recipe(raw: Optional[str]) -> RecipeName:
    if not raw:
        return "talking_head"
    key = str(raw).strip().lower()
    if key in VALID_RECIPES:
        return key  # type: ignore[return-value]
    return "talking_head"


def normalize_intensity(raw: Optional[str]) -> IntensityName:
    if not raw:
        return "balanced"
    key = str(raw).strip().lower()
    if key in VALID_INTENSITIES:
        return key  # type: ignore[return-value]
    return "balanced"


def resolve_recipe(
    recipe: Optional[str] = None,
    intensity: Optional[str] = None,
) -> RecipeConfig:
    """
    Resolve manifest fields to a RecipeConfig.

    When POLISH_RECIPES=0, always return talking_head (legacy path).
    Intensity is accepted but not scaled yet (Phase 4 deferred).
    """
    if not polish_recipes_enabled():
        name: RecipeName = "talking_head"
        inten: IntensityName = "balanced"
    else:
        name = normalize_recipe(recipe)
        inten = normalize_intensity(intensity)

    spec = _RECIPE_SPECS[name]
    return RecipeConfig(
        name=name,
        intensity=inten,
        max_broll_count=spec.get("max_broll_count"),
        min_broll_seconds=spec.get("min_broll_seconds"),
        max_broll_seconds=spec.get("max_broll_seconds"),
        min_gap_between_brolls=spec.get("min_gap_between_brolls"),
        min_hook_aroll_seconds=spec.get("min_hook_aroll_seconds"),
        min_end_aroll_seconds=spec.get("min_end_aroll_seconds"),
        min_aroll_share=spec.get("min_aroll_share"),
        zoom_policy=spec.get("zoom_policy", "alternate_segment"),
        caption_pack=spec.get("caption_pack", "default"),
        sfx_enabled=bool(spec.get("sfx_enabled", False)),
        craft_brief=str(spec.get("craft_brief") or ""),
    )


def resolve_recipe_for_project(project_dir) -> RecipeConfig:
    """Load project.json and resolve recipe; safe fallback to talking_head."""
    try:
        from .project_store import load_manifest

        m = load_manifest(project_dir)
        return resolve_recipe(
            getattr(m, "recipe", None),
            getattr(m, "intensity", None),
        )
    except Exception:
        return resolve_recipe()


def select_zoom_ranges(
    aroll_segments: Sequence[dict[str, Any]],
    policy: ZoomPolicy = "alternate_segment",
) -> set[tuple[float, float]]:
    """
    Choose A-roll windows for segment zoom.

    `alternate_segment` matches pre-recipe render (zoom every other A-roll, starting
    with no-zoom on index 0).
    """
    zoom_ranges: set[tuple[float, float]] = set()
    for i, seg in enumerate(aroll_segments):
        start = float(seg["start"])
        end = float(seg["end"])
        if end <= start:
            continue
        if policy == "alternate_segment":
            if i % 2 == 1:
                zoom_ranges.add((start, end))
        elif policy == "dense_alternate":
            # Denser: zoom all A-roll after the hook segment.
            if i > 0:
                zoom_ranges.add((start, end))
        elif policy == "sparse_presence":
            if i % 3 == 1:
                zoom_ranges.add((start, end))
        else:
            # Unknown → legacy alternate
            if i % 2 == 1:
                zoom_ranges.add((start, end))
    return zoom_ranges
