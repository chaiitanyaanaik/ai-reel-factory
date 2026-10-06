"""Lightweight safety checks for user-authored B-roll edit prompts.

Gemini/Veo also enforce their own policies; this is an early, cheap gate for
clearly disallowed requests before we spend rewrite + generation calls.
"""
from __future__ import annotations

import re

UNSAFE_EDIT_MESSAGE = (
    "That edit isn't allowed. Avoid sexual, nude, violent, hateful, or abusive "
    "content — describe a safe visual change instead."
)

# Obvious intent phrases / terms. Keep tight to reduce false positives.
_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(nude|nudity|naked|topless|bottomless|lingerie|nsfw)\b",
        r"\b(porn|pornograph(?:y|ic)|xxx|onlyfans)\b",
        r"\b(sex(?:ual)?|intercourse|orgasm|erotic|fetish)\b",
        r"\b(genitals?|penis|vagina|breast(?:s)?|nipples?)\b",
        r"\b(child\s*porn|csam|underage\s*sex)\b",
        r"\b(rape|molest(?:ation|ing)?|grop(?:e|ing))\b",
        r"\b(behead(?:ing)?|dismember(?:ment|ing)?|gore|gory|torture)\b",
        r"\b(kill(?:ing)?|murder(?:ing)?|shoot(?:ing)?)\s+(?:him|her|them|someone|people|kids?|children)\b",
        r"\b(hate\s*speech|racial\s*slur|lynch(?:ing)?)\b",
        r"\b(slur|nazi|swastika)\b",
    )
)

_REFUSAL_MARKERS = (
    "i can't",
    "i cannot",
    "i won't",
    "not allowed",
    "unsafe",
    "refused",
    "refuse",
    "disallowed",
    "against policy",
    "cannot fulfill",
    "can't fulfill",
)


def looks_unsafe_edit(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _UNSAFE_PATTERNS)


def assert_safe_broll_edit(message: str) -> None:
    """Raise ValueError when the user edit is clearly disallowed."""
    if looks_unsafe_edit(message):
        raise ValueError(UNSAFE_EDIT_MESSAGE)


def rewrite_looks_like_refusal(prompt: str) -> bool:
    """True if the rewrite model refused instead of returning a Veo prompt."""
    text = (prompt or "").strip().lower()
    if not text:
        return True
    if len(text) < 40 and any(m in text for m in _REFUSAL_MARKERS):
        return True
    # Longer refusals often start with a refusal phrase.
    head = text[:160]
    return any(head.startswith(m) or f"{m}" in head[:80] for m in ("i can't", "i cannot", "i won't", "sorry,"))
