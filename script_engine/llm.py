"""
Shared Gemini LLM client for script and editing plan generation.
Uses the same API key as Veo (GOOGLE_API_KEY or GEMINI_API_KEY).
"""
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _get_api_key() -> str:
    _load_dotenv()
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    return key


def gemini_text(prompt: str, model: str | None = None) -> str:
    """Send a text prompt to Gemini and return the response text."""
    try:
        from google import genai
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text


def gemini_json(prompt: str, model: str | None = None) -> dict:
    """Send a prompt expecting JSON back. Strips markdown fences if present."""
    raw = gemini_text(prompt, model=model)
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text.strip())


def load_style_guide(project_dir: Path | None = None) -> dict:
    """Load style guide -- project-level overrides global default."""
    guide = {}
    global_path = ROOT / "style_guide.json"
    if global_path.exists():
        guide = json.loads(global_path.read_text(encoding="utf-8"))
    if project_dir:
        project_path = project_dir / "style_guide.json"
        if project_path.exists():
            guide.update(json.loads(project_path.read_text(encoding="utf-8")))
    return guide


def style_guide_text(project_dir: Path | None = None) -> str:
    """Return style guide as a readable string for LLM prompts."""
    guide = load_style_guide(project_dir)
    if not guide:
        return ""
    lines = ["## Style Guide"]
    for key, val in guide.items():
        if key != "filler_clip":
            lines.append(f"- {key.replace('_', ' ').title()}: {val}")
    return "\n".join(lines)


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
