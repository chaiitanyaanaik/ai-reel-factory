"""
Google Veo API client for AI-generated B-roll clips.
Uses Gemini API (google-genai). Set GOOGLE_API_KEY or GEMINI_API_KEY in env or .env.
"""
from pathlib import Path
from dataclasses import dataclass
import json
import os
import time

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class VeoGenerateResult:
    path: Path
    full_prompt: str
    model: str
    duration_seconds: int
    latency_ms: float
    used_image: bool = False
    estimated_cost_usd: float = 0.0
    suggestion: str = ""


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
    spoken_line: str | None = None,
) -> None:
    """Set VEO_LOG_PROMPTS=1 to print and append prompts. Optional VEO_PROMPT_LOG=path/to/file.log."""
    if os.environ.get("VEO_LOG_PROMPTS", "0").strip().lower() not in ("1", "true", "yes"):
        return
    raw = os.environ.get("VEO_PROMPT_LOG", "").strip()
    log_file = Path(raw) if raw else (Path(output_path).parent / "veo_prompts.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    spoken_block = (spoken_line or "").strip() or "(none)"
    block = (
        f"\n{'=' * 60}\n"
        f"output_file: {Path(output_path).name}\n"
        f"duration_seconds: {duration_seconds}\n"
        f"VEO_EXPAND_PROMPT was used: {gemini_expanded}\n"
        f"--- spoken line (context) ---\n{spoken_block}\n\n"
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


def load_style_guide(
    project_dir: Path | None = None,
    *,
    user_id: str | None = None,
) -> dict:
    """Load style guide: global → user brand → optional project override."""
    from core.user_brand import load_merged_style_guide

    return load_merged_style_guide(project_dir, user_id=user_id)


def _build_style_suffix(style: dict, kinetic: bool = False) -> str:
    """
    Short positive-only style suffix for Veo.

    Negatives ("avoid …") are omitted — Veo often mishandles them.
    Casting is always included when present for reel-wide consistency.
    """
    slow_tokens = ("cinematic", "smooth", "sweeping", "dreamy", "slow-motion", "gliding", "graceful")
    if not style:
        return (
            "Vertical 9:16, continuous motion, must look like real video not a photograph."
        )
    parts: list[str] = []

    fmt = style.get("format") or "vertical 9:16, must look like real video not a photograph"
    if kinetic and any(tok in fmt.lower() for tok in slow_tokens):
        fmt = "vertical 9:16, must look like real video not a photograph"
    parts.append(fmt)

    if style.get("visual_tone"):
        vt = str(style["visual_tone"])
        if kinetic and any(tok in vt.lower() for tok in slow_tokens):
            vt = "hyper-kinetic, gritty, dynamic motion, fast-paced"
        parts.append(vt)

    if style.get("color_palette"):
        parts.append(f"Colors: {style['color_palette']}")

    # Reel-wide casting — keep people looking like the same household.
    if style.get("broll_casting"):
        parts.append(str(style["broll_casting"]).strip())
    elif style.get("camera_style") and not kinetic:
        # Lightweight camera hint only when no casting (avoid doubling "Camera:" with shot move).
        cs = str(style["camera_style"])
        if "b-roll" in cs.lower() or "broll" in cs.lower():
            parts.append(cs)

    if kinetic:
        parts.append(
            "motion blur, high-speed dynamic movement, fast shutter, hyper-kinetic, high energy"
        )

    # Never append style["avoid"] — negatives confuse Veo.
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def _expand_prompt_with_gemini(
    raw_suggestion: str,
    kinetic: bool = False,
    spoken_line: str | None = None,
) -> str:
    """Optionally expand a short B-roll suggestion into a Veo-optimized prompt using Gemini."""
    try:
        from script_engine.llm import gemini_text
    except ImportError:
        return raw_suggestion

    spoken_block = ""
    if spoken_line and spoken_line.strip():
        spoken_block = (
            f"Spoken line during this cutaway (illustrate this, do not show text on screen):\n"
            f"\"{spoken_line.strip()}\"\n\n"
        )

    if kinetic:
        prompt = (
            "You are a technical cinematographer writing prompts for an AI video generator. "
            "Your goal is to create fast-paced, high-energy B-roll for Instagram Reels. "
            "Describe scenes using literal, physical terms and start in media res (action already in motion). "
            "Use aggressive verbs (rushing, darting, whipping) and explicit kinetic camera movements "
            "(whip pans, rapid tracking, FPV drone). "
            "Do NOT use metaphors, abstractions, or subjective emotions "
            "(no 'apprehension', 'fear', 'hope', 'tension' — show body language instead). "
            "Do NOT describe slow, sweeping, or graceful movements. "
            "Keep the prompt under 50 words. "
            "Output only the final prompt, nothing else.\n\n"
            f"{spoken_block}"
            f"B-roll idea: {raw_suggestion}"
        )
    else:
        prompt = (
            "Convert this B-roll idea into ONE detailed video-generation prompt. "
            "Formula: [Shot type/camera move] + [Subject] + [Concrete physical action] + [Environment/lighting]. "
            "Be specific (e.g. 'close-up', 'slow dolly-in', observable body language). "
            "Do NOT use emotion adjectives — show them as actions "
            "(frozen hands, eyes darting to a parent, quick nod while looking down). "
            "Single scene only, no dialogue, no on-screen text. "
            "Output only the prompt, no explanation.\n\n"
            f"{spoken_block}"
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
    spoken_line: str | None = None,
) -> str:
    """
    Build a Veo-optimized prompt from an editing-plan suggestion + optional spoken line.
    Google's recommended formula: [Cinematography] + [Subject] + [Action] + [Context] + [Style].
    """
    spoken = (spoken_line or "").strip()
    if use_gemini_expand:
        suggestion = _expand_prompt_with_gemini(
            suggestion, kinetic=kinetic, spoken_line=spoken or None
        )

    # Explicit temporal framing helps Veo understand the clip as a single shot.
    head = f"Single continuous {duration_seconds} second clip. "

    # Spoken-line context first — strongest relevance signal for the cutaway.
    context = ""
    if spoken:
        # Cap length so style/shot details still fit.
        clipped = spoken if len(spoken) <= 220 else spoken[:217].rstrip() + "…"
        context = (
            f'Illustrates the spoken line (do not render text or lipsync): "{clipped}". '
        )

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
    full = f"{head}{context}{content}, {style_suffix}"
    if "real video" not in full.lower() and "not a photograph" not in full.lower():
        full += " Must look like real video, not a photograph."
    return full


def _clamp_veo_duration(seconds: int) -> int:
    """Veo 3.1 Lite/Fast accept 4, 6, or 8 seconds only."""
    allowed = (4, 6, 8)
    if seconds in allowed:
        return seconds
    return min(allowed, key=lambda x: abs(x - int(seconds)))


def _default_veo_model() -> str:
    _load_dotenv()
    return os.environ.get("VEO_MODEL", "veo-3.1-lite-generate-preview").strip()


def spoken_text_for_range(
    project_dir: Path | None,
    start: float,
    end: float,
) -> str:
    """
    Collect Whisper transcript text overlapping [start, end] (A-roll timeline seconds).
    Used to ground Veo prompts in what is being said during the cutaway.
    """
    if project_dir is None or end <= start:
        return ""
    path = Path(project_dir) / "transcripts" / "transcript.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    parts: list[str] = []
    for seg in data.get("segments") or []:
        try:
            s = float(seg.get("start", 0))
            e = float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if e <= start or s >= end:
            continue
        text = (seg.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def generate_video(
    prompt: str,
    output_path: Path,
    duration_seconds: int = 4,
    aspect_ratio: str = "9:16",
    project_dir: Path | None = None,
    spoken_line: str | None = None,
    *,
    image_path: Path | None = None,
    full_prompt_override: str | None = None,
    return_result: bool = False,
) -> Path | VeoGenerateResult:
    """
    Generate a short video clip with Veo and save to output_path.

    When full_prompt_override is set, that string is sent as-is (edit path).
    When image_path is set, prefer image-to-video; fall back to text-only on failure.
    Set return_result=True to get VeoGenerateResult (prompt, latency, cost).
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    from core.tracing import estimate_veo_cost_usd, observe

    client = genai.Client(api_key=get_api_key())
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration_seconds = _clamp_veo_duration(duration_seconds)
    resolution = os.environ.get("VEO_RESOLUTION", "720p").strip() or "720p"
    gen_audio = os.environ.get("VEO_GENERATE_AUDIO", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    style = load_style_guide(project_dir)
    kinetic = _resolve_kinetic_mode()
    style_suffix = _build_style_suffix(style, kinetic=kinetic)
    use_expand = os.environ.get("VEO_EXPAND_PROMPT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    spoken = (spoken_line or "").strip() or None
    if full_prompt_override and full_prompt_override.strip():
        full_prompt = full_prompt_override.strip()
        use_expand = False
    else:
        full_prompt = _build_veo_prompt(
            prompt,
            duration_seconds,
            style_suffix,
            use_gemini_expand=use_expand,
            kinetic=kinetic,
            spoken_line=spoken,
        )
    _log_veo_prompt_if_enabled(
        output_path, prompt, full_prompt, duration_seconds, use_expand, spoken
    )
    model = _default_veo_model()
    print(
        f"  [Veo] model={model} duration={duration_seconds}s "
        f"resolution={resolution} audio={gen_audio}"
        + (
            f" spoken={spoken[:60]!r}…"
            if spoken and len(spoken) > 60
            else (f" spoken={spoken!r}" if spoken else "")
        )
        + (" image=yes" if image_path else "")
    )

    config_kwargs: dict = {
        "aspect_ratio": aspect_ratio,
        "number_of_videos": 1,
        "duration_seconds": duration_seconds,
        "resolution": resolution,
    }
    if gen_audio:
        config_kwargs["generate_audio"] = True
    config = types.GenerateVideosConfig(**config_kwargs)

    img_path = Path(image_path) if image_path else None
    used_image = False

    def _make_source(*, with_image: bool):
        if with_image and img_path and img_path.exists():
            data = img_path.read_bytes()
            mime = "image/jpeg"
            if img_path.suffix.lower() == ".png":
                mime = "image/png"
            try:
                image = types.Image(image_bytes=data, mime_type=mime)
                return types.GenerateVideosSource(prompt=full_prompt, image=image), True
            except Exception as e:
                print(f"  [Veo] Could not build image source ({e}); text-only")
                return types.GenerateVideosSource(prompt=full_prompt), False
        return types.GenerateVideosSource(prompt=full_prompt), False

    t0 = time.monotonic()
    with observe(
        "veo.generate",
        as_type="generation",
        input={"prompt": full_prompt[:2000], "suggestion": prompt[:500]},
        model=model,
        metadata={
            "duration_seconds": duration_seconds,
            "resolution": resolution,
            "has_image": bool(img_path and img_path.exists()),
        },
    ) as span:
        last_err: Exception | None = None
        operation = None
        source, used_image = _make_source(with_image=True)
        attempts_plan = [(source, used_image)]
        if used_image:
            # Fallback plan if I2V rejected
            text_source, _ = _make_source(with_image=False)
            attempts_plan.append((text_source, False))

        for source, use_img in attempts_plan:
            used_image = use_img
            for attempt in range(1, 4):
                try:
                    operation = client.models.generate_videos(
                        model=model,
                        source=source,
                        config=config,
                    )
                    break
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    hard_quota = (
                        "exceeded your current quota" in msg.lower()
                        or "check your plan and billing" in msg.lower()
                    )
                    if hard_quota:
                        raise
                    # Image not supported → try text-only outer loop
                    if use_img and (
                        "image" in msg.lower()
                        or "invalid" in msg.lower()
                        or "not support" in msg.lower()
                    ):
                        print(f"  [Veo] Image seed rejected, falling back to text-only: {e}")
                        operation = None
                        break
                    if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and attempt < 3:
                        wait = 5 * attempt
                        print(f"  [Veo] Rate limit (attempt {attempt}/3), waiting {wait}s…")
                        time.sleep(wait)
                        continue
                    raise
            if operation is not None:
                break

        if operation is None:
            raise last_err or RuntimeError("Veo generate_videos failed")

        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)

        if not operation.response or not operation.response.generated_videos:
            err = getattr(operation, "error", None) or "No video in response"
            raise RuntimeError(f"Veo failed: {err}")

        generated = operation.response.generated_videos[0]
        client.files.download(file=generated.video)
        generated.video.save(str(output_path))

        latency_ms = (time.monotonic() - t0) * 1000
        cost = estimate_veo_cost_usd(duration_seconds)
        span.update(
            output={"path": str(output_path.name)},
            cost_usd=cost,
            metadata={
                "latency_ms": round(latency_ms, 1),
                "used_image": used_image,
                "duration_seconds": duration_seconds,
            },
        )

    result = VeoGenerateResult(
        path=output_path,
        full_prompt=full_prompt,
        model=model,
        duration_seconds=duration_seconds,
        latency_ms=round(latency_ms, 1),
        used_image=used_image,
        estimated_cost_usd=cost,
        suggestion=prompt,
    )
    if return_result:
        return result
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
    out = generate_video(
        prompt,
        output_path,
        duration_seconds=5,
        aspect_ratio=aspect_ratio,
        project_dir=project_dir,
    )
    return out if isinstance(out, Path) else out.path
