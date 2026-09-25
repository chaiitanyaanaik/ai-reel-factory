"""
B-roll: script-aligned clips sourced from Pexels (free) with Veo fallback.
Timeline drives when to show A-roll vs B-roll; each B-roll segment gets a clip.
Priority: 1) existing clip on disk, 2) Pexels stock, 3) Veo AI generation.
"""
from pathlib import Path
import json
import os


def _load_dotenv() -> None:
    """Load env vars from .env when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")
    except ImportError:
        pass


def load_timeline(project_dir: Path) -> tuple[list[dict], list[dict]]:
    path = project_dir / "cuts" / "timeline.json"
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("timeline", []), data.get("filler_ranges", [])


def get_broll_for_fillers(project_dir: Path) -> list[tuple[float, float, Path | None]]:
    cuts_path = project_dir / "cuts" / "filler_segments.json"
    if not cuts_path.exists():
        return []
    segments = json.loads(cuts_path.read_text(encoding="utf-8"))
    filler_clip = project_dir / "broll" / "filler.mp4"
    return [(s["start"], s["end"], filler_clip if filler_clip.exists() else None) for s in segments]


def prepare_broll_placeholders(project_dir: Path) -> None:
    broll_dir = project_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)
    (broll_dir / "README.txt").write_text(
        "B-roll clips: Pexels stock (free) or Veo AI-generated. "
        "filler.mp4 for um/uh segments.",
        encoding="utf-8",
    )


def _resolve_use_veo() -> bool:
    """Check env to determine if Veo fallback is enabled."""
    _load_dotenv()
    return os.environ.get("BROLL_USE_VEO", "1").strip().lower() not in ("0", "false", "no")


def _resolve_veo_only() -> bool:
    """If true, skip Pexels and generate all B-roll with Veo."""
    _load_dotenv()
    return os.environ.get("BROLL_VEO_ONLY", "0").strip().lower() in ("1", "true", "yes")


def _try_pexels(suggestion: str, output_path: Path) -> bool:
    """Try downloading a clip from Pexels. Returns True on success."""
    try:
        from .pexels_client import download_video, get_pexels_key
        if not get_pexels_key():
            return False
        result = download_video(suggestion, output_path)
        return result is not None
    except Exception as e:
        print(f"  [Pexels] Failed: {e}")
        return False


def _try_pexels_filler(output_path: Path) -> bool:
    """Try downloading a filler clip from Pexels. Returns True on success."""
    try:
        from .pexels_client import download_filler, get_pexels_key
        if not get_pexels_key():
            return False
        result = download_filler(output_path)
        return result is not None
    except Exception as e:
        print(f"  [Pexels] Filler failed: {e}")
        return False


def _try_veo(suggestion: str, output_path: Path, project_dir: Path) -> bool:
    """Try generating a clip with Veo. Returns True on success."""
    try:
        from .veo_client import generate_video
        generate_video(suggestion, output_path, duration_seconds=5,
                       aspect_ratio="9:16", project_dir=project_dir)
        print(f"  [Veo] Generated: {output_path.name}")
        return True
    except Exception as e:
        raise RuntimeError(f"Veo failed: {e}") from e


def _try_veo_filler(output_path: Path, project_dir: Path) -> bool:
    """Try generating a filler clip with Veo. Returns True on success."""
    try:
        from .veo_client import generate_filler_clip
        generate_filler_clip(output_path, aspect_ratio="9:16", project_dir=project_dir)
        print(f"  [Veo] Generated filler: {output_path.name}")
        return True
    except Exception as e:
        raise RuntimeError(f"Veo filler failed: {e}") from e


def generate_broll_from_timeline(
    project_dir: Path,
    use_veo: bool | None = None,
) -> None:
    """
    For each broll/filler segment in the timeline, source a clip:
      1. Skip if clip already exists on disk
      2. Try Pexels (free stock video), unless BROLL_VEO_ONLY=1
      3. Fall back to Veo (AI generation) if Pexels has no match or was skipped

    Set BROLL_USE_VEO=0 in env to disable Veo fallback entirely.
    Set BROLL_VEO_ONLY=1 in env to skip Pexels and create all B-roll with Veo.
    Set PEXELS_API_KEY in env to enable Pexels.
    """
    if use_veo is None:
        use_veo = _resolve_use_veo()
    veo_only = _resolve_veo_only()
    if veo_only:
        print("  [B-roll] Veo-only mode: skipping Pexels, generating all clips with Veo.")

    timeline, _ = load_timeline(project_dir)
    if not timeline:
        prepare_broll_placeholders(project_dir)
        return

    broll_dir = project_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    pexels_count = 0
    veo_count = 0
    skipped_count = 0

    for seg in timeline:
        seg_type = seg.get("type")

        if seg_type == "broll":
            idx = seg.get("broll_index")
            if idx is None:
                continue
            path = broll_dir / f"{idx:03d}.mp4"
            if path.exists():
                skipped_count += 1
                continue

            suggestion = (seg.get("suggestion") or "abstract professional clip").strip()
            if not suggestion:
                suggestion = "abstract professional B-roll"

            # Try Pexels first (unless Veo-only mode)
            if not veo_only and _try_pexels(suggestion, path):
                pexels_count += 1
                continue

            # Fall back to Veo (or use Veo directly when BROLL_VEO_ONLY=1)
            if use_veo:
                _try_veo(suggestion, path, project_dir)
                veo_count += 1
            else:
                print(f"  [SKIP] No Pexels match and Veo disabled: {path.name}")

        elif seg_type == "filler":
            path = broll_dir / "filler.mp4"
            if path.exists():
                skipped_count += 1
                continue

            if not veo_only and _try_pexels_filler(path):
                pexels_count += 1
                continue

            if use_veo:
                _try_veo_filler(path, project_dir)
                veo_count += 1
            else:
                print("  [SKIP] No Pexels filler and Veo disabled")

    print(f"  B-roll summary: {pexels_count} Pexels, {veo_count} Veo, {skipped_count} existing")
