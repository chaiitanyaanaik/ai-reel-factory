"""
Final render: timeline-driven A-roll / B-roll / optional filler -> 1080x1920 reel.
Effects: 1.2x speed, CapCut-style slow zoom-in on A-roll windows, B-roll fades, white flash.
Audio: only from merged (speaker) track — B-roll inputs use video only, never mixed in.
Speaker gain: SPEAKER_VOLUME_DB (default 2.5 dB). Filler segments: B-roll + muted audio if present.
"""
from pathlib import Path
import json
import os
import subprocess
from typing import Iterable


WIDTH, HEIGHT = 1080, 1920
SPEED = 1.2
FADE_DURATION = 0.25
FLASH_DURATION = 0.08


def _speaker_volume_suffix() -> str:
    """FFmpeg volume filter after atempo; set SPEAKER_VOLUME_DB=0 to disable."""
    raw = os.environ.get("SPEAKER_VOLUME_DB", "2.5").strip()
    if not raw or raw.lower() in ("0", "off", "none"):
        return ""
    try:
        db = float(raw)
    except ValueError:
        db = 2.5
    if abs(db) < 0.01:
        return ""
    return f",volume={db}dB"


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


_ALLOWED_X264_PRESETS = frozenset(
    {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    }
)


def _x264_preset() -> str:
    """
    libx264 speed/quality tradeoff via RENDER_X264_PRESET.
    Faster = quicker encode, larger/lower-efficiency file. Default: veryfast (good for cloud CPUs).
    """
    raw = (os.environ.get("RENDER_X264_PRESET") or "veryfast").strip().lower()
    if raw in _ALLOWED_X264_PRESETS:
        return raw
    return "veryfast"


def _video_encode_args() -> list[str]:
    return ["-c:v", "libx264", "-preset", _x264_preset()]



def _animated_zoom_multiplier_expr(
    zoom_ranges: Iterable[tuple[float, float]],
    *,
    speed: float,
    factor: float,
    ramp_seconds: float,
) -> str:
    """
    FFmpeg expression for scale multiplier (eval=frame).

    Outside zoom windows → 1. Inside → ease 1→factor over ramp_seconds, then hold.
    Commas escaped for filtergraph (\\,).
    """
    amp = max(0.0, float(factor) - 1.0)
    ramp = max(0.3, float(ramp_seconds))
    progresses: list[str] = []
    for s, e in sorted(zoom_ranges):
        ss = float(s) / speed
        ee = float(e) / speed
        if ee <= ss:
            continue
        # Progress 0→1 over the first `ramp` seconds of the window.
        progresses.append(
            f"if(between(t\\,{ss:.3f}\\,{ee:.3f})\\,"
            f"min(1\\,(t-{ss:.3f})/{ramp:.3f})\\,0)"
        )
    if not progresses:
        return "1"
    prog = progresses[0]
    for p in progresses[1:]:
        prog = f"max({prog}\\,{p})"
    if amp < 1e-6:
        return "1"
    return f"(1+{amp:.4f}*({prog}))"


def _audio_filter_chain(
    mute_expr: str | None = None,
    *,
    already_enhanced: bool = False,
) -> str:
    """
    Build FFmpeg audio filters for the speaker track.

    If the input is already merged/enhanced.mp4, only speed + gain (+ mutes):
    re-running denoise/compress/loudnorm pumps residual room noise.
    """
    parts: list[str] = []
    parts.append(f"atempo={SPEED}")

    light = already_enhanced and _truthy_env("RENDER_LIGHT_AUDIO_ON_ENHANCED", "1")
    if not light:
        hp = _int_env("AUDIO_HIGHPASS_HZ", 100)
        lp = _int_env("AUDIO_LOWPASS_HZ", 12000)
        if hp > 0:
            parts.append(f"highpass=f={hp}")
        if lp > 0:
            parts.append(f"lowpass=f={lp}")

        # Denoise runs in enhance stage; avoid double-processing here.
        if _truthy_env("AUDIO_COMPRESS", "1"):
            makeup = _float_env("AUDIO_COMPRESS_MAKEUP", 1.5)
            parts.append(
                f"acompressor=threshold=-18dB:ratio=3:attack=5:release=120:makeup={makeup}"
            )
            parts.append("alimiter=limit=0.95")

        if _truthy_env("AUDIO_LOUDNORM", "0"):
            parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    vol = _speaker_volume_suffix().lstrip(",")
    if vol:
        parts.append(vol)

    if mute_expr:
        parts.append(mute_expr)

    return ",".join(parts)


def _video_scale_filter(width: int, height: int) -> str:
    flags = os.environ.get("SCALE_FLAGS", "lanczos").strip() or "lanczos"
    base = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags={flags},"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    # Apply optional clarity filters after scaling/pad so A-roll and B-roll match.
    if _truthy_env("VIDEO_DENOISE", "0"):
        # Light spatial denoise; conservative values.
        base += ",hqdn3d=1.5:1.5:6:6"
    if _truthy_env("VIDEO_SHARPEN", "1"):
        # Mild sharpen; avoid halos.
        base += ",unsharp=5:5:0.35:5:5:0.0"
    return base


def _broll_overlay_filter(
    input_label: str,
    out_label: str,
    *,
    sped_duration: float,
    sped_start: float,
    scale_filter: str,
    fade_d: float,
) -> str:
    """
    Trim/scale/fade a B-roll clip and delay PTS so overlays stay clip-length in memory.

    Prefer setpts delay over tpad=start_duration (which materializes full-timeline
    black frames and spikes RAM on 8GB hosts).
    """
    fade_out_start = max(0.0, sped_duration - fade_d)
    return (
        f"[{input_label}]trim=0:{sped_duration},setpts=PTS-STARTPTS,"
        f"{scale_filter},"
        f"fade=type=in:start_time=0:duration={fade_d}:color=black,"
        f"fade=type=out:start_time={fade_out_start:.3f}:duration={fade_d}:color=black,"
        f"setpts=PTS+{sped_start:.3f}/TB"
        f"[{out_label}]"
    )


def _broll_flash_filters(
    flash_windows: list[tuple[float, float]],
    *,
    width: int,
    height: int,
    flash_d: float,
) -> list[str]:
    """
    Short white flash sources (duration ≈ flash_d), delayed to each B-roll start.

    Avoids a full-timeline color=d=… source + split, which is heavy on RAM.
    """
    parts: list[str] = []
    for j, (fs, _fe) in enumerate(flash_windows):
        parts.append(
            f"color=c=white:s={width}x{height}:r=30:d={flash_d:.3f}[flashraw{j}];"
            f"[flashraw{j}]setpts=PTS-STARTPTS+{fs:.3f}/TB[flashsrc{j}]"
        )
    return parts


def load_timeline(project_dir: Path) -> list[dict]:
    path = project_dir / "cuts" / "timeline.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("timeline", [])


def render_final(
    project_dir: Path,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    from .enhance import resolve_source_video

    try:
        merged = resolve_source_video(project_dir)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Merged video not found: {e}") from e

    already_enhanced = merged.name == "enhanced.mp4"
    final_dir = project_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    out_path = final_dir / "reel.mp4"
    broll_dir = project_dir / "broll"

    timeline = load_timeline(project_dir)
    if not timeline:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(merged),
                "-vf", (
                    f"setpts=PTS/{SPEED},"
                    f"{_video_scale_filter(width, height)}"
                ),
                "-af", _audio_filter_chain(already_enhanced=already_enhanced),
                *_video_encode_args(),
                "-c:a", "aac",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return out_path

    # Collect broll/filler overlays (video only from clip inputs — no B-roll audio in output)
    overlays = []
    for seg in timeline:
        t = seg.get("type", "aroll")
        if t == "aroll":
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        duration = end - start
        if duration <= 0:
            continue
        if t == "filler":
            clip = broll_dir / "filler.mp4"
        else:
            idx = seg.get("broll_index")
            clip = broll_dir / f"{idx:03d}.mp4" if isinstance(idx, int) else broll_dir / "filler.mp4"
        if not clip.exists():
            continue
        overlays.append({
            "start": start,
            "end": end,
            "duration": duration,
            "clip": clip,
            "mute": t == "filler",
        })

    if not overlays:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(merged),
                "-vf", (
                    f"setpts=PTS/{SPEED},"
                    f"{_video_scale_filter(width, height)}"
                ),
                "-af", _audio_filter_chain(already_enhanced=already_enhanced),
                *_video_encode_args(),
                "-c:a", "aac",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return out_path

    # Zoom windows from style recipe (talking_head = legacy every-other A-roll)
    from core.recipes import resolve_recipe_for_project, select_zoom_ranges

    aroll_segments = [s for s in timeline if s.get("type") == "aroll"]
    recipe_cfg = resolve_recipe_for_project(project_dir)
    zoom_ranges = select_zoom_ranges(aroll_segments, recipe_cfg.zoom_policy)

    inputs = ["-i", str(merged)]
    for ov in overlays:
        inputs.extend(["-i", str(ov["clip"])])

    scale_filter = _video_scale_filter(width, height)
    # CapCut-style slow zoom-in (animated), not a static crop hold.
    zoom_factor = max(1.05, min(_float_env("ZOOM_FACTOR", 1.35), 1.8))
    zoom_ramp = max(0.3, min(_float_env("ZOOM_RAMP_SECONDS", 2.5), 8.0))

    filter_parts = []

    # Step 1: Speed up merged video + scale
    filter_parts.append(
        f"[0:v]setpts=PTS/{SPEED},{scale_filter}[scaled]"
    )

    # Step 2: White flash at start
    filter_parts.append(
        f"[scaled]fade=type=in:start_time=0:duration={FLASH_DURATION}:color=white[flashed]"
    )

    # Step 3: Animated slow zoom-in on selected A-roll windows (visible motion).
    # Static crop-holds are easy to miss; CapCut-style ramps 1→factor over ZOOM_RAMP_SECONDS.
    if zoom_ranges:
        z_expr = _animated_zoom_multiplier_expr(
            zoom_ranges,
            speed=SPEED,
            factor=zoom_factor,
            ramp_seconds=zoom_ramp,
        )
        filter_parts.append(
            f"[flashed]scale=w='iw*{z_expr}':h='ih*{z_expr}':eval=frame,"
            f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2[base]"
        )
    else:
        filter_parts.append("[flashed]copy[base]")

    # Step 4: Scale, trim, fade, and PTS-delay each broll overlay (no tpad).
    for i, ov in enumerate(overlays):
        idx = i + 1
        sped_duration = ov["duration"] / SPEED
        sped_start = ov["start"] / SPEED
        fade_d = min(FADE_DURATION, sped_duration / 3)
        filter_parts.append(
            _broll_overlay_filter(
                f"{idx}:v",
                f"ov{i}",
                sped_duration=sped_duration,
                sped_start=sped_start,
                scale_filter=scale_filter,
                fade_d=fade_d,
            )
        )

    # Step 5: Overlay broll — PTS delay + enable= keeps timing without black padding.
    current = "base"
    for i, ov in enumerate(overlays):
        out_label = f"v{i}"
        sped_start = ov["start"] / SPEED
        sped_end = ov["end"] / SPEED
        filter_parts.append(
            f"[{current}][ov{i}]overlay=0:0:"
            f"enable='between(t,{sped_start:.3f},{sped_end:.3f})':"
            f"eof_action=pass[{out_label}]"
        )
        current = out_label

    # Step 5c: Optional short white flashes at B-roll start times
    if _truthy_env("FLASH_ON_BROLL", "1"):
        try:
            flash_d = float(os.environ.get("FLASH_DURATION_FLASH", "0.06"))
        except ValueError:
            flash_d = 0.06
        flash_d = max(0.01, min(flash_d, 0.2))

        flash_windows = [
            (ov["start"] / SPEED, (ov["start"] / SPEED) + flash_d)
            for ov in overlays
            if not ov.get("mute")
        ]
        if flash_windows:
            filter_parts.extend(
                _broll_flash_filters(
                    flash_windows,
                    width=width,
                    height=height,
                    flash_d=flash_d,
                )
            )
            for j, (fs, fe) in enumerate(flash_windows):
                out_label = f"flashout{j}"
                filter_parts.append(
                    f"[{current}][flashsrc{j}]overlay=0:0:"
                    f"enable='between(t,{fs:.3f},{fe:.3f})':"
                    f"eof_action=pass[{out_label}]"
                )
                current = out_label

    # Step 6: Speaker audio chain (denoise/EQ/dynamics/loudness/gain) + optional filler mutes
    filler_segs = [ov for ov in overlays if ov["mute"]]
    if filler_segs:
        mute_expr = ",".join(
            f"volume=enable='between(t,{f['start']/SPEED:.3f},{f['end']/SPEED:.3f})':volume=0"
            for f in filler_segs
        )
        filter_parts.append(
            f"[0:a]{_audio_filter_chain(mute_expr=mute_expr, already_enhanced=already_enhanced)}[aout]"
        )
        audio_map = "[aout]"
    else:
        filter_parts.append(
            f"[0:a]{_audio_filter_chain(already_enhanced=already_enhanced)}[aout]"
        )
        audio_map = "[aout]"

    # Use ';' only — newlines inside filter_complex confuse some FFmpeg builds.
    filter_complex = ";".join(filter_parts)
    if _truthy_env("RENDER_DEBUG_FILTERS", "0"):
        print(f"[DEBUG] filter_complex:\n{filter_complex}")

    preset = _x264_preset()
    print(f"[render] libx264 preset={preset}")
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{current}]",
        "-map", audio_map,
        *_video_encode_args(),
        "-c:a", "aac",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[DEBUG] FFmpeg stderr:\n{result.stderr[-2000:]}")
        result.check_returncode()

    return out_path
