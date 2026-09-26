"""Lip-sync safety: cleaned transcript must keep the same spoken words."""
from __future__ import annotations

import re
from typing import Iterable


_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def word_overlap_ratio(source: str | Iterable[str], candidate: str) -> float:
    """
    Fraction of source words that appear in candidate (order-insensitive multiset).

    Returns 1.0 if source is empty.
    """
    if isinstance(source, str):
        src = normalize_words(source)
    else:
        src = [w.lower() for w in source if w]
    cand = normalize_words(candidate)
    if not src:
        return 1.0
    from collections import Counter

    src_c = Counter(src)
    cand_c = Counter(cand)
    matched = 0
    for w, n in src_c.items():
        matched += min(n, cand_c.get(w, 0))
    return matched / len(src)


def assert_lip_sync_safe(
    transcript_text: str,
    cleaned_script: str,
    threshold: float = 0.85,
) -> float:
    """
    Raise ValueError if cleaned_script rewrites spoken dialogue.

    threshold: minimum fraction of transcript words that must appear in cleaned script.
    """
    ratio = word_overlap_ratio(transcript_text, cleaned_script)
    if ratio < threshold:
        raise ValueError(
            f"Lip-sync guard failed: word overlap {ratio:.2%} < {threshold:.0%}. "
            "Cleaned script must not rewrite spoken dialogue."
        )
    return ratio


def transcript_plain_text(transcript: dict) -> str:
    """Flatten Whisper result to plain text."""
    if transcript.get("text"):
        return str(transcript["text"]).strip()
    parts = []
    for seg in transcript.get("segments") or []:
        t = (seg.get("text") or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts)
