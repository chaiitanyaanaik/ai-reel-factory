"""
Google Veo API client for AI-generated B-roll clips.
Uses Gemini API (google-genai). Set GOOGLE_API_KEY or GEMINI_API_KEY in env or .env.
"""
from pathlib import Path
import json
import os
import time

ROOT = Path(__file__).resolve().parent.parent


def _resolve_kinetic_mode() -> bool:
    """Return True when IG-style kinetic B-roll prompts should be used."""
    return os.environ.get("VEO_KINETIC_IG", "0").strip().lower() in ("1", "true", "yes")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _log_veo_prompt_if_enabled(
    output_path: Path,
    timeline_suggestion: str,
    full_prompt: str,
    duration_seconds: int,
    gemini_expanded: bool,
) -> None:
    """Set VEO_LOG_PROMPTS=1 to print and append prompts. Optional VEO_PROMPT_LOG=path/to/file.log."""
    if os.environ.get("VEO_LOG_PROMPTS", "0").strip().lower() not in ("1", "true", "yes"):
        return
    raw = os.environ.get("VEO_PROMPT_LOG", "").strip()
    log_file = Path(raw) if raw else (Path(output_path).parent / "veo_prompts.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    block = (
        f"\n{'=' * 60}\n"
        f"output_file: {Path(output_path).name}\n"
        f"duration_seconds: {duration_seconds}\n"
        f"VEO_EXPAND_PROMPT was used: {gemini_expanded}\n"
        f"--- timeline B-roll suggestion (input) ---\n{timeline_suggestion}\n\n"
        f"--- full string sent to Veo ---\n{full_prompt}\n"
    )
    with log_file.open("a", encoding="utf-8") as f:
        f.write(block)
    print(block)


def get_api_key() -> str:
    _load_dotenv()
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY for Veo video generation. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    return key


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


def _build_style_suffix(style: dict, kinetic: bool = False) -> str:
    """
    Convert style guide dict into a prompt suffix for Veo.

    When kinetic=True, we:
    - Avoid including "avoid ..." negative phrases directly in the prompt
    - Downweight or omit slow/floaty language like "cinematic", "smooth", "dreamy"
    - Optionally inject kinetic tone words suitable for fast IG B-roll
    """
    slow_tokens = ("cinematic", "smooth", "sweeping", "dreamy", "slow-motion", "gliding", "graceful")
    if not style:
        return (
            "Continuous fluid motion throughout the entire clip. "
            "Cinematic 35mm lens feel, vertical 9:16 format. "
            "This must look like a real video, not a photograph."
        )
    parts: list[str] = []

    if style.get("format"):
        fmt = style["format"]
        if kinetic:
            lower = fmt.lower()
            if any(tok in lower for tok in slow_tokens):
                # Keep aspect ratio / realism, drop slow adjectives
                if "9:16" in fmt:
                    fmt = "vertical 9:16, must look like real video not a photograph"
                else:
                    fmt = "must look like real video not a photograph"
        parts.append(fmt)
    else:
        parts.append("vertical 9:16, continuous fluid motion, must look like real video not a photograph")

    if style.get("visual_tone"):
        vt = style["visual_tone"]
        if kinetic:
            lower = vt.lower()
            if any(tok in lower for tok in slow_tokens):
                # Replace slow tone with kinetic descriptors
                vt = "hyper-kinetic, gritty, dynamic motion, fast-paced"
        parts.append(f"Visual tone: {vt}")

    if style.get("camera_style"):
        cs = style["camera_style"]
        if kinetic:
            lower = cs.lower()
            if any(tok in lower for tok in slow_tokens):
                cs = "aggressive handheld, rapid tracking moves"
        parts.append(f"Camera: {cs}")

    if style.get("mood"):
        parts.append(f"Mood: {style['mood']}")
    if style.get("color_palette"):
        parts.append(f"Colors: {style['color_palette']}")

    # Casting / locale for people in B-roll (always appended so Veo respects it in kinetic mode too).
    if style.get("broll_casting"):
        parts.append(str(style["broll_casting"]).strip())

    # Do not inject "avoid ..." strings into the positive prompt in kinetic mode.
    if style.get("avoid") and not kinetic:
        parts.append(style["avoid"])

    if kinetic:
        parts.append(
            "motion blur, high-speed dynamic movement, fast shutter, hyper-kinetic, high energy"
        )

    return ". ".join(parts) + "."


def _expand_prompt_with_gemini(raw_suggestion: str, kinetic: bool = False) -> str:
    """Optionally expand a short B-roll suggestion into a Veo-optimized prompt using Gemini."""
    try:
        from script_engine.llm import gemini_text
    except ImportError:
        return raw_suggestion
    if kinetic:
        prompt = (
            "You are a technical cinematographer writing prompts for an AI video generator. "
            "Your goal is to create fast-paced, high-energy B-roll for Instagram Reels. "
            "Describe scenes using literal, physical terms and start in media res (action already in motion). "
            "Use aggressive verbs (rushing, darting, whipping) and explicit kinetic camera movements "
            "(whip pans, rapid tracking, FPV drone). "
            "Do NOT use metaphors, abstractions, or subjective emotions. "
            "Do NOT describe slow, sweeping, or graceful movements. "
            "Keep the prompt under 50 words. "
            "Output only the final prompt, nothing else.\n\n"
            f"B-roll idea: {raw_suggestion}"
        )
    else:
        prompt = (
            "Convert this B-roll idea into ONE detailed video-generation prompt. "
            "Use this formula: [Shot type/camera move] + [Subject] + [Action] + [Environment/lighting]. "
            "Be specific (e.g. 'close-up', 'slow dolly-in', concrete actions). "
            "Single scene only, no dialogue, no text. Output only the prompt, no explanation.\n\n"
            f"B-roll idea: {raw_suggestion}"
        )
    try:
        out = gemini_text(prompt).strip()
        return out if out else raw_suggestion
    except Exception:
        return raw_suggestion


def _build_veo_prompt(
    suggestion: str,
    duration_seconds: int,
    style_suffix: str,
    use_gemini_expand: bool = False,
    kinetic: bool = False,
) -> str:
    """
    Build a Veo-optimized prompt from an editing-plan suggestion.
    Google's recommended formula: [Cinematography] + [Subject] + [Action] + [Context] + [Style].
    """
    if use_gemini_expand:
        suggestion = _expand_prompt_with_gemini(suggestion, kinetic=kinetic)

    # Explicit temporal framing helps Veo understand the clip as a single shot.
    head = f"Single continuous {duration_seconds} second clip. "

    # Parse "Subject. Camera: move. Scene detail." into components.
    subject_action = suggestion.strip().rstrip(".")
    scene_detail = ""
    camera_move = ""

    if "Camera:" in suggestion:
        before, after = suggestion.split("Camera:", 1)
        subject_action = before.strip().rstrip(".")
        tail = after.strip()
        if ". " in tail:
            first, rest = tail.split(". ", 1)
            camera_move = first.strip().rstrip(".")
            scene_detail = rest.strip().rstrip(".")
        else:
            camera_move = tail.rstrip(".")

    # Subject-first architecture: [Subject/Action] + [Scene] + [Camera]
    parts: list[str] = []
    if subject_action:
        parts.append(subject_action)
    if scene_detail:
        parts.append(scene_detail)
    if camera_move:
        parts.append(f"Camera: {camera_move}")

    content = ", ".join(parts) if parts else suggestion
    full = f"{head}{content}, {style_suffix}"
    if "real video" not in full.lower() and "not a photograph" not in full.lower():
        full += " Must look like real video, not a photograph."
    return full


def generate_video(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 5,
    aspect_ratio: str = "9:16",
    project_dir: Path | None = None,
) -> Path:
    """Generate a short video clip with Veo and save to output_path."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    client = genai.Client(api_key=get_api_key())
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    style = load_style_guide(project_dir)
    kinetic = _resolve_kinetic_mode()
    style_suffix = _build_style_suffix(style, kinetic=kinetic)
    use_expand = os.environ.get("VEO_EXPAND_PROMPT", "0").strip().lower() in ("1", "true", "yes")
    full_prompt = _build_veo_prompt(
        prompt,
        duration_seconds,
        style_suffix,
        use_gemini_expand=use_expand,
        kinetic=kinetic,
    )
    _log_veo_prompt_if_enabled(
        output_path, prompt, full_prompt, duration_seconds, use_expand
    )
    source = types.GenerateVideosSource(prompt=full_prompt)
    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        number_of_videos=1,
    )
    operation = client.models.generate_videos(
        model=os.environ.get("VEO_MODEL", "veo-3.0-generate-001"),
        source=source,
        config=config,
    )

    while not operation.done:
        time.sleep(10)
        operation = client.operations.get(operation)

    if not operation.response or not operation.response.generated_videos:
        err = getattr(operation, "error", None) or "No video in response"
        raise RuntimeError(f"Veo failed: {err}")

    generated = operation.response.generated_videos[0]
    client.files.download(file=generated.video)
    generated.video.save(str(output_path))
    return output_path


def generate_filler_clip(
    output_path: Path,
    aspect_ratio: str = "9:16",
    project_dir: Path | None = None,
) -> Path:
    """Generate a quick B-roll clip for filler segments (um/uh). Muted in final mix."""
    style = load_style_guide(project_dir)
    prompt = style.get(
        "filler_clip",
        "Soft sunlight streaming through a window into a warm living room. "
        "Camera: slow steady pan from left to right. "
        "Dust particles floating in the light beams, curtains swaying gently. "
        "No people, no text."
    )
    return generate_video(prompt, output_path, duration_seconds=5,
                          aspect_ratio=aspect_ratio, project_dir=project_dir)
