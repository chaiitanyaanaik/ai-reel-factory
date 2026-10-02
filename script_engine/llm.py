"""
Shared Gemini LLM client for script and editing plan generation.
Uses the same API key as Veo (GOOGLE_API_KEY or GEMINI_API_KEY).
Retries on 503 and falls back across models when capacity is tight.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import time
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

# Tried in order when primary is overloaded / unavailable
_DEFAULT_FALLBACKS = (
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
)


def _get_api_key() -> str:
    from core.config import load_env

    load_env()
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    return key


def _model_chain(preferred: str | None) -> list[str]:
    from core.config import load_env

    load_env()
    primary = preferred or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    extra = os.environ.get("GEMINI_MODEL_FALLBACKS", "").strip()
    fallbacks = [m.strip() for m in extra.split(",") if m.strip()] if extra else list(_DEFAULT_FALLBACKS)
    chain: list[str] = []
    for m in [primary, *fallbacks]:
        if m and m not in chain:
            chain.append(m)
    return chain


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "503",
            "unavailable",
            "high demand",
            "resource_exhausted",
            "429",
            "temporarily",
        )
    )


def _usage_from_response(response: Any) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return 0, 0
    inp = int(getattr(meta, "prompt_token_count", 0) or 0)
    out = int(
        getattr(meta, "candidates_token_count", None)
        or getattr(meta, "response_token_count", None)
        or 0
    )
    return inp, out


def _build_contents(prompt: str, image_path: Path | None = None) -> Any:
    if image_path is None:
        return prompt
    from google.genai import types

    data = Path(image_path).read_bytes()
    mime = "image/jpeg"
    suf = Path(image_path).suffix.lower()
    if suf == ".png":
        mime = "image/png"
    elif suf in (".webp",):
        mime = "image/webp"
    return [
        types.Part.from_bytes(data=data, mime_type=mime),
        prompt,
    ]


def gemini_text(
    prompt: str,
    model: str | None = None,
    *,
    image_path: Path | str | None = None,
    span_name: str = "gemini.generate",
) -> str:
    """Send a text (optional image) prompt to Gemini and return the response text."""
    try:
        from google import genai
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    from core.tracing import (
        estimate_gemini_cost_usd,
        observe,
    )

    client = genai.Client(api_key=_get_api_key())
    chain = _model_chain(model)
    img = Path(image_path) if image_path else None
    contents = _build_contents(prompt, img if img and img.exists() else None)
    last_err: Exception | None = None

    with observe(
        span_name,
        as_type="generation",
        input={"prompt": prompt[:2000], "has_image": bool(img and img.exists())},
        metadata={"image": str(img) if img else None},
    ) as span:
        for i, candidate in enumerate(chain):
            for attempt in range(3):
                try:
                    if i > 0 or attempt > 0:
                        print(f"  [Gemini] Trying {candidate} (attempt {attempt + 1})")
                    response = client.models.generate_content(
                        model=candidate, contents=contents
                    )
                    text = response.text
                    if not text:
                        raise RuntimeError(f"Empty response from {candidate}")
                    inp, out = _usage_from_response(response)
                    cost = estimate_gemini_cost_usd(inp, out)
                    span.update(
                        output=text[:2000],
                        model=candidate,
                        usage={"input": inp, "output": out, "total": inp + out},
                        cost_usd=cost,
                        metadata={"model": candidate},
                    )
                    return text
                except Exception as e:
                    last_err = e
                    if _is_retryable(e) and attempt < 2:
                        wait = 2 ** attempt
                        print(f"  [Gemini] {candidate} busy; retry in {wait}s")
                        time.sleep(wait)
                        continue
                    if _is_retryable(e):
                        print(f"  [Gemini] {candidate} unavailable, trying next model…")
                        break
                    raise
        raise RuntimeError(f"All Gemini models failed. Last error: {last_err}") from last_err


def gemini_json(
    prompt: str,
    model: str | None = None,
    *,
    image_path: Path | str | None = None,
    span_name: str = "gemini.json",
) -> dict:
    """Send a prompt expecting JSON back. Strips markdown fences if present."""
    raw = gemini_text(
        prompt, model=model, image_path=image_path, span_name=span_name
    )
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1 :]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text.strip())


def load_style_guide(
    project_dir: Path | None = None,
    *,
    user_id: str | None = None,
) -> dict:
    """Load style guide: global → user brand → optional project override."""
    from core.user_brand import load_merged_style_guide

    return load_merged_style_guide(project_dir, user_id=user_id)


def style_guide_text(
    project_dir: Path | None = None,
    *,
    user_id: str | None = None,
) -> str:
    """Return style guide as a readable string for LLM prompts."""
    from core.user_brand import style_guide_text as _style_text

    return _style_text(project_dir, user_id=user_id)


def load_references(*names: str) -> str:
    """Load reference files from references/ and return as a single prompt block.

    Usage: load_references("hook-patterns", "writing-styles", "viral-angles")
    """
    refs_dir = ROOT / "references"
    parts = []
    for name in names:
        path = refs_dir / f"{name}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)
