"""
Generate an Instagram Reel cover from an existing rendered reel.

Flow:
1) Read full script artifacts for context.
2) Extract candidate A-roll frames from final/reel.mp4.
3) Pick the best candidate frame.
4) Use AI image generation with frame-derived subject context and strict cover rules.
5) Save final cover + prompt + metadata/debug artifacts.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRAME_TIMES = (0.3, 0.8, 1.2, 1.8)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "FFmpeg not found. Install it and add it to your PATH.\n"
            "  Windows: winget install FFmpeg\n"
            "  Or download from https://ffmpeg.org/download.html\n"
            "  Then restart your terminal."
        )


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _detect_global_style_references() -> list[Path]:
    """Load global style references from repo root for all projects."""
    candidates = [
        ROOT / "cover_reference_style_01.png",
        ROOT / "cover_reference_style_01.png.png",
        ROOT / "cover_reference_style_02.png",
        ROOT / "cover_reference_style_02.png.png",
        ROOT / "cover_reference_style.png",
    ]
    refs: list[Path] = []
    for p in candidates:
        if p.exists() and p.is_file():
            refs.append(p)
    return refs


def _mime_type_for_image(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "image/png"


def _clean_line(line: str) -> str:
    line = re.sub(r"[*_#>`-]+", " ", line).strip()
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -:;")


def _derive_headline(raw_script: str, final_script: dict) -> str:
    """Pick a concise headline candidate while still using full script context."""
    beats = final_script.get("beats", []) if isinstance(final_script, dict) else []
    for beat in beats:
        if beat.get("type") == "aroll":
            suggestion = _clean_line(str(beat.get("suggestion", "")))
            if len(suggestion.split()) >= 4:
                return suggestion[:72]

    for line in raw_script.splitlines():
        cleaned = _clean_line(line)
        if not cleaned:
            continue
        words = cleaned.split()
        if len(words) < 4:
            continue
        return cleaned[:72]

    return "Watch This Before You Judge"


def _optimize_headline_with_ai(raw_headline: str, raw_script: str) -> str:
    """
    Use Gemini text to sharpen the headline while keeping it short and hook-worthy.
    Falls back to the deterministic headline when LLM call fails.
    """
    _load_dotenv()
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return raw_headline
    try:
        from script_engine.llm import gemini_text
        prompt = (
            "Rewrite this reel cover headline to be high-impact and concise.\n"
            "Rules:\n"
            "- Keep ONE headline only (no subtext).\n"
            "- 4 to 11 words.\n"
            "- Strong hook language.\n"
            "- Keep the same core meaning.\n"
            "- Output only the rewritten headline.\n\n"
            f"Base headline: {raw_headline}\n\n"
            f"Full script context:\n{raw_script}"
        )
        out = gemini_text(prompt).strip()
        out = _clean_line(out)
        if 3 <= len(out.split()) <= 14:
            return out[:84]
    except Exception:
        pass
    return raw_headline


def build_cover_prompt_context(project_dir: Path) -> dict:
    raw_script = _read_text(project_dir / "raw_script.md")
    final_script = _read_json(project_dir / "final_script.json")
    headline = _derive_headline(raw_script, final_script)
    headline = _optimize_headline_with_ai(headline, raw_script)
    return {
        "headline_text": headline,
        "full_script_context": raw_script,
        "final_script_context": final_script,
    }


def extract_candidate_frames(project_dir: Path, times: tuple[float, ...] = DEFAULT_FRAME_TIMES) -> list[Path]:
    _check_ffmpeg()
    reel = project_dir / "final" / "reel.mp4"
    if not reel.exists():
        raise FileNotFoundError(f"Rendered reel not found: {reel}")

    frame_dir = project_dir / "cover" / "frame_candidates"
    frame_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []

    for i, ts in enumerate(times, start=1):
        out = frame_dir / f"frame_{i:02d}_{str(ts).replace('.', '_')}s.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{ts:.3f}",
            "-i", str(reel),
            "-frames:v", "1",
            "-q:v", "2",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            out_paths.append(out)
    if not out_paths:
        raise RuntimeError("Failed to extract candidate cover frames from reel video.")
    return out_paths


def select_best_frame(frame_paths: list[Path]) -> Path:
    """
    Simple heuristic: largest JPEG often preserves more detail and less compression blur.
    """
    return max(frame_paths, key=lambda p: p.stat().st_size)


def _load_style_guide(project_dir: Path) -> dict:
    guide = {}
    global_path = ROOT / "style_guide.json"
    if global_path.exists():
        guide = json.loads(global_path.read_text(encoding="utf-8"))
    project_path = project_dir / "style_guide.json"
    if project_path.exists():
        guide.update(json.loads(project_path.read_text(encoding="utf-8")))
    return guide


def _describe_reference_frame(frame_path: Path) -> str:
    """Use Gemini vision to describe the selected A-roll frame."""
    _load_dotenv()
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Set GOOGLE_API_KEY or GEMINI_API_KEY before running cover generation.")

    client = genai.Client(api_key=api_key)
    model = os.environ.get("COVER_VISION_MODEL", os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"))
    img_bytes = frame_path.read_bytes()
    prompt = (
        "Describe this person/frame for recreating an Instagram cover image. "
        "Include visible clothing, pose, expression, face angle, and composition. "
        "Be concise and literal. No brand names."
    )
    resp = client.models.generate_content(
        model=model,
        contents=[
            prompt,
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
        ],
    )
    desc = (resp.text or "").strip()
    return desc or "talking-head creator portrait, direct eye contact, neutral expression"


def _build_cover_prompt(
    context: dict,
    project_dir: Path,
) -> str:
    style = _load_style_guide(project_dir)
    style_hint = style.get("visual_tone", "")
    headline = context["headline_text"].upper()
    style_refs = _detect_global_style_references()
    
    style_ref_note = f"Images 2 through {len(style_refs) + 1} are style references." if style_refs else "No style references provided."

    return f"""Role: Senior Art Director for a high-end fashion and lifestyle magazine.
Task: Create an editorial Instagram Reel cover.

Input Assets Mapping (CRITICAL):
- Image [1] (The Anchor): CREATOR IDENTITY. You must use this for the person's exact face, hair texture, skin tone, and identity.
- {style_ref_note} Emulate the typography weight, organic spacing, and hand-drawn accents seen here.

Headline Text: "{headline}"

Spacial Directives & Layout (MANDATORY):
1. NO COLUMNS / NO SPLIT SCREENS: Do not create a symmetrical 2-column layout or a rigid text box next to an image box.
2. OVERLAP & DEPTH: The subject must be large and positioned on the right (taking up ~60% of the canvas). Their shoulder, arm, or hair MUST organically overlap or "bleed" into the text area on the left.
3. GROUNDING: The subject's torso should be grounded at the bottom of the frame. Do not make them look like a floating sticker. 
4. DEPTH OF FIELD: Use a shallow f/1.8 aesthetic. The subject is tack-sharp, but the background should be a slightly textured, creamy off-white studio wall with very subtle bokeh.

Typography & Design:
- FONT: Heavy, premium Sans-Serif (like Helvetica Neue Bold, Inter, or Arial Black). Left-aligned.
- STYLING: Make one keyword in the headline a different color (muted deep red, brown, or gold).
- ACCENTS: Add a subtle, organic, hand-drawn underline or arrow pointing toward the creator, matching the style references.
- INTEGRATION: Text should feel printed directly onto the scene, interacting with the negative space. No drop shadows.

Core Rule: 
This must NOT look like a Canva template. It should feel like a $50/issue independent magazine cover where the typography and photography interact.

Style Guide Hint: {style_hint}
"""


def _extract_generated_image_bytes(response) -> bytes | None:
    """Best-effort parser for google-genai image responses across model variants."""
    generated_images = getattr(response, "generated_images", None)
    if generated_images:
        for item in generated_images:
            image_obj = getattr(item, "image", None)
            image_bytes = getattr(image_obj, "image_bytes", None) if image_obj else None
            if image_bytes:
                return image_bytes

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            continue
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None) if inline_data else None
            if data:
                return data
    return None


def _compose_cover_layout(
    selected_frame: Path,
    headline_text: str,
    out_path: Path,
    background_hint: Path | None = None,
) -> None:
    """
    Deterministic local compositor so hook text + image are always present.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        # Hard fallback: at least return selected frame.
        out_path.write_bytes(selected_frame.read_bytes())
        return

    width, height = 1080, 1920
    canvas = Image.new("RGB", (width, height), "white")

    if background_hint and background_hint.exists():
        try:
            bg = Image.open(background_hint).convert("RGB").resize((width, height))
            # Fade AI background strongly so text remains readable and style stays minimal.
            overlay = Image.new("RGB", (width, height), "white")
            bg = Image.blend(bg, overlay, 0.82)
            canvas.paste(bg, (0, 0))
        except Exception:
            pass

    frame = Image.open(selected_frame).convert("RGB")
    # Place speaker image at lower-right with soft edge fade.
    target_w = int(width * 0.58)
    scale = target_w / frame.width
    target_h = int(frame.height * scale)
    frame = frame.resize((target_w, target_h))
    x = width - target_w - 36
    y = height - target_h - 80

    mask = Image.new("L", frame.size, 255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=24))
    canvas.paste(frame, (x, y), mask)

    draw = ImageDraw.Draw(canvas)
    # Font fallback chain.
    font_candidates = [
        "arialbd.ttf",
        "Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
    ]
    title_font = None
    for fp in font_candidates:
        try:
            title_font = ImageFont.truetype(fp, 104)
            break
        except Exception:
            continue
    if title_font is None:
        title_font = ImageFont.load_default()

    words = headline_text.upper().split()
    if len(words) <= 4:
        line1 = " ".join(words)
        line2 = ""
    else:
        split_at = min(max(2, len(words) // 2), 5)
        line1 = " ".join(words[:split_at])
        line2 = " ".join(words[split_at:])

    tx = 64
    ty = 130
    draw.text((tx, ty), line1, font=title_font, fill=(196, 27, 35))
    if line2:
        draw.text((tx, ty + 128), line2, font=title_font, fill=(25, 28, 32))

    canvas.save(out_path, format="JPEG", quality=95)


def generate_cover_with_model(
    project_dir: Path,
    selected_frame: Path,
    headline_text: str,
    prompt_text: str,
) -> tuple[Path, bool, str]:
    """
    AI-first cover generation.
    Returns (cover_path, used_fallback, error_message).
    """
    _load_dotenv()
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("Install google-genai: pip install google-genai")

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Set GOOGLE_API_KEY or GEMINI_API_KEY before running cover generation.")

    cover_dir = project_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    final_dir = project_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    out_path = final_dir / "cover.jpg"

    selected_copy = cover_dir / "selected_frame.jpg"
    selected_copy.write_bytes(selected_frame.read_bytes())

    client = genai.Client(api_key=api_key)
    model = os.environ.get("COVER_IMAGE_MODEL", "gemini-3-pro-image-preview")

    # Construct the deterministic part payload: [Image 1: Subject] -> [Images 2+: Styles] -> [Text Prompt]
    base_parts = [
        types.Part.from_bytes(data=selected_frame.read_bytes(), mime_type="image/jpeg"),
    ]
    for ref in _detect_global_style_references():
        base_parts.append(
            types.Part.from_bytes(
                data=ref.read_bytes(),
                mime_type=_mime_type_for_image(ref),
            )
        )
    base_parts.append(prompt_text)

    used_fallback = False
    error_message = ""
    temp_ai_path = cover_dir / "ai_image.jpg"

    try:
        # Primary Pipeline
        response = client.models.generate_content(
            model=model,
            contents=base_parts,
        )
        image_bytes = _extract_generated_image_bytes(response)
        if not image_bytes:
            raise RuntimeError("Primary Image API returned no image bytes.")
        temp_ai_path.write_bytes(image_bytes)
        out_path.write_bytes(image_bytes)

    except Exception as e:
        error_message = f"Primary model failed: {e}"
        try:
            # Secondary/Fallback Pipeline
            used_fallback = True
            secondary_model = os.environ.get("COVER_IMAGE_FALLBACK_MODEL", "gemini-3.1-flash-image-preview")
            
            # Use the exact same parts payload, ensuring determinism across models
            resp2 = client.models.generate_content(
                model=secondary_model,
                contents=base_parts,
            )
            image_bytes = _extract_generated_image_bytes(resp2)
            if not image_bytes:
                raise RuntimeError("Secondary model returned no image bytes.")
            temp_ai_path.write_bytes(image_bytes)
            out_path.write_bytes(image_bytes)
            
        except Exception as e2:
            error_message = f"{error_message} | Secondary model failed: {e2}"
            raise RuntimeError(error_message)

    return out_path, used_fallback, error_message

def generate_cover_from_reel(project_dir: Path) -> Path:
    context = build_cover_prompt_context(project_dir)
    candidates = extract_candidate_frames(project_dir)
    selected_frame = select_best_frame(candidates)
    prompt_text = _build_cover_prompt(context, project_dir)
    cover_path, used_fallback, error_message = generate_cover_with_model(
        project_dir=project_dir,
        selected_frame=selected_frame,
        headline_text=context["headline_text"],
        prompt_text=prompt_text,
    )

    cover_dir = project_dir / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    (cover_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")

    metadata = {
        "headline_text": context["headline_text"],
        "selected_frame": str(selected_frame),
        "frame_candidates": [str(p) for p in candidates],
        "cover_output": str(cover_path),
        "used_fallback": used_fallback,
        "error_message": error_message,
        "cover_image_model": os.environ.get("COVER_IMAGE_MODEL", "gemini-3-pro-image-preview"),
        "cover_require_ai": os.environ.get("COVER_REQUIRE_AI", "1"),
        "global_style_references_used": [str(p) for p in _detect_global_style_references()],
    }
    (cover_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return cover_path
