"""
Audio enhance stage: denoise + social loudness on merged video.

Writes merged/enhanced.mp4. Downstream stages prefer enhanced when present.

Default denoise: DeepFilterNet3 (extract WAV → enhance → remux + FFmpeg post).
Fallback: FFmpeg afftdn when AUDIO_DENOISE=afftdn or DeepFilterNet is unavailable.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from core.config import env_float, load_env, truthy


def _afftdn_filter() -> str:
    """Spectral denoise. FFmpeg afftdn nf range is [-80, -20]; nr is reduction dB."""
    nf = max(-80.0, min(env_float("AUDIO_DENOISE_NF", -25), -20.0))
    nr = max(0.01, min(env_float("AUDIO_DENOISE_NR", 10), 97.0))
    return f"afftdn=nf={nf}:nr={nr}"


def resolve_denoise_mode() -> str:
    """Return 'off', 'afftdn', or 'deepfilternet'."""
    load_env()
    denoise = os.environ.get("AUDIO_DENOISE", "deepfilternet").strip().lower()
    if denoise in ("0", "false", "off", "none"):
        return "off"
    if denoise in ("afftdn", "1", "true", "yes"):
        return "afftdn"
    if denoise in ("deepfilternet", "df", "deepfilter"):
        return "deepfilternet"
    # Legacy RNNoise env values → afftdn (models removed).
    if denoise == "arnndn":
        return "afftdn"
    return "deepfilternet"


def resolve_source_video(project_dir: Path) -> Path:
    """Prefer enhanced.mp4 when present, else merged.mp4."""
    enhanced = project_dir / "merged" / "enhanced.mp4"
    if enhanced.exists():
        return enhanced
    merged = project_dir / "merged" / "merged.mp4"
    if merged.exists():
        return merged
    raise FileNotFoundError(f"No merged video in {project_dir / 'merged'}")


def _pre_post_chain(*, include_afftdn: bool) -> str:
    """High/low pass, optional afftdn, dynamics, loudnorm, gain."""
    parts: list[str] = []
    hp = int(env_float("AUDIO_HIGHPASS_HZ", 100))
    lp = int(env_float("AUDIO_LOWPASS_HZ", 10000))
    if hp > 0:
        parts.append(f"highpass=f={hp}")
    if lp > 0:
        parts.append(f"lowpass=f={lp}")

    if include_afftdn:
        parts.append(_afftdn_filter())

    if truthy("AUDIO_COMPRESS", "1"):
        makeup = env_float("AUDIO_COMPRESS_MAKEUP", 1.0)
        parts.append(
            f"acompressor=threshold=-20dB:ratio=2.2:attack=8:release=160:makeup={makeup}"
        )
        parts.append("alimiter=limit=0.95")

    if truthy("AUDIO_LOUDNORM", "1"):
        i_lufs = env_float("AUDIO_LOUDNORM_I", -16)
        parts.append(f"loudnorm=I={i_lufs}:TP=-1.5:LRA=11")

    gain_db = env_float("SPEAKER_VOLUME_DB", 2.0)
    if abs(gain_db) >= 0.01:
        parts.append(f"volume={gain_db}dB")

    return ",".join(parts) if parts else "anull"


def social_audio_filters() -> str:
    """Single-stream -af chain for FFmpeg-only enhance path."""
    load_env()
    if (os.environ.get("AUDIO_PRESET", "social").strip().lower() or "social") == "off":
        return "anull"
    mode = resolve_denoise_mode()
    chain = _pre_post_chain(include_afftdn=(mode == "afftdn"))
    return chain


def build_enhance_ffmpeg_audio_args() -> list[str]:
    """FFmpeg args for remux with filtered audio (afftdn / off modes only)."""
    load_env()
    if (os.environ.get("AUDIO_PRESET", "social").strip().lower() or "social") == "off":
        return ["-af", "anull"]
    chain = social_audio_filters()
    return ["-af", chain]


def _extract_wav(merged: Path, wav_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(merged),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Audio extract failed:\n{result.stderr[-2000:] if result.stderr else 'unknown'}"
        )


def _remux_with_audio(
    merged: Path,
    audio_source: Path,
    out_path: Path,
    *,
    audio_is_wav: bool,
) -> None:
    mode = resolve_denoise_mode()
    include_afftdn = mode == "afftdn" and not audio_is_wav
    af = _pre_post_chain(include_afftdn=include_afftdn)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(merged),
        "-i",
        str(audio_source),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-af",
        af,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Audio remux failed:\n{result.stderr[-2000:] if result.stderr else 'unknown'}"
        )


def _enhance_with_deepfilternet(merged: Path, out_path: Path) -> None:
    from video_engine.deepfilter_denoise import deepfilter_available, denoise_wav

    if not deepfilter_available():
        raise RuntimeError("DeepFilterNet not installed (pip install deepfilternet soundfile)")

    with tempfile.TemporaryDirectory(prefix="enhance-df-") as tmp:
        tmp_dir = Path(tmp)
        raw_wav = tmp_dir / "in.wav"
        clean_wav = tmp_dir / "out.wav"
        _extract_wav(merged, raw_wav)
        denoise_wav(raw_wav, clean_wav)
        _remux_with_audio(merged, clean_wav, out_path, audio_is_wav=True)


def _enhance_with_ffmpeg(
    merged: Path,
    out_path: Path,
    *,
    include_afftdn: bool | None = None,
) -> None:
    load_env()
    preset = (os.environ.get("AUDIO_PRESET", "social").strip().lower() or "social")
    if preset == "off":
        chain = "anull"
    else:
        if include_afftdn is None:
            include_afftdn = resolve_denoise_mode() == "afftdn"
        chain = _pre_post_chain(include_afftdn=include_afftdn)
    audio_args = ["-af", chain]
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(merged),
        *audio_args,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Audio enhance failed:\n{result.stderr[-2000:] if result.stderr else 'unknown'}"
        )


def enhance_audio(project_dir: Path) -> Path:
    """
    Remux merged.mp4 with enhanced audio → merged/enhanced.mp4.
    Video stream is copied; audio is denoised and leveled.
    """
    merged = project_dir / "merged" / "merged.mp4"
    if not merged.exists():
        raise FileNotFoundError(f"Run merge first: {merged}")

    out_dir = project_dir / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "enhanced.mp4"

    load_env()
    mode = resolve_denoise_mode()
    if mode == "deepfilternet":
        try:
            _enhance_with_deepfilternet(merged, out_path)
            return out_path
        except Exception as exc:
            import sys

            print(
                f"[enhance] DeepFilterNet failed ({exc}); falling back to afftdn",
                file=sys.stderr,
            )
            _enhance_with_ffmpeg(merged, out_path, include_afftdn=True)
            return out_path

    _enhance_with_ffmpeg(merged, out_path)
    return out_path
