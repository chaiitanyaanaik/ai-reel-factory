"""
Audio enhance stage: opinionated FFmpeg social preset on merged video.

Writes merged/enhanced.mp4. Downstream stages prefer enhanced when present.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.config import env_float, load_env, truthy


def resolve_source_video(project_dir: Path) -> Path:
    """Prefer enhanced.mp4 when present, else merged.mp4."""
    enhanced = project_dir / "merged" / "enhanced.mp4"
    if enhanced.exists():
        return enhanced
    merged = project_dir / "merged" / "merged.mp4"
    if merged.exists():
        return merged
    raise FileNotFoundError(f"No merged video in {project_dir / 'merged'}")


def social_audio_filters() -> str:
    """
    Build FFmpeg audio filter chain for AUDIO_PRESET=social (default).

    highpass → lowpass → afftdn → compressor → limiter → loudnorm → volume
    """
    load_env()
    preset = os.environ.get("AUDIO_PRESET", "social").strip().lower() or "social"

    if preset == "off":
        return "anull"

    hp = int(env_float("AUDIO_HIGHPASS_HZ", 80))
    lp = int(env_float("AUDIO_LOWPASS_HZ", 10000))
    gain_db = env_float("SPEAKER_VOLUME_DB", 2.5)

    parts: list[str] = []
    if hp > 0:
        parts.append(f"highpass=f={hp}")
    if lp > 0:
        parts.append(f"lowpass=f={lp}")

    denoise = os.environ.get("AUDIO_DENOISE", "afftdn").strip().lower()
    if denoise in ("0", "false", "off", "none"):
        pass
    elif denoise == "arnndn":
        model = os.environ.get("ARNNDN_MODEL_PATH", "").strip()
        if model:
            parts.append(f"arnndn=m='{model}'")
        else:
            parts.append("afftdn=nf=-25")
    else:
        # afftdn (default for social) or explicit afftdn / 1
        parts.append("afftdn=nf=-25")

    if truthy("AUDIO_COMPRESS", "1"):
        parts.append("acompressor=threshold=-18dB:ratio=3:attack=5:release=120:makeup=3")
        parts.append("alimiter=limit=0.95")

    if truthy("AUDIO_LOUDNORM", "1"):
        parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    if abs(gain_db) >= 0.01:
        parts.append(f"volume={gain_db}dB")

    return ",".join(parts) if parts else "anull"


def enhance_audio(project_dir: Path) -> Path:
    """
    Remux merged.mp4 with enhanced audio → merged/enhanced.mp4.
    Video stream is copied; audio is filtered.
    """
    merged = project_dir / "merged" / "merged.mp4"
    if not merged.exists():
        raise FileNotFoundError(f"Run merge first: {merged}")

    out_dir = project_dir / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "enhanced.mp4"

    af = social_audio_filters()
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(merged),
        "-c:v",
        "copy",
        "-af",
        af,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Audio enhance failed:\n{result.stderr[-2000:] if result.stderr else 'unknown error'}"
        )
    return out_path
