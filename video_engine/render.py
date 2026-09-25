"""
Final render: timeline-driven A-roll / B-roll / optional filler -> 1080x1920 reel.
Effects: 1.2x speed, alternating zoom cuts, fade-in/out on broll, white flash at start.
Audio: only from merged (speaker) track — B-roll inputs use video only, never mixed in.
Speaker gain: SPEAKER_VOLUME_DB (default 2.5 dB). Filler segments: B-roll + muted audio if present.
"""
from pathlib import Path
import json
import os
import subprocess


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


def _audio_filter_chain(mute_expr: str | None = None) -> str:
    """
    Build FFmpeg audio filters for the speaker track.
    Order: atempo -> (HP/LP) -> (denoise) -> (compress/limit) -> (loudnorm) -> (speaker gain) -> (mute_expr)
    """
    parts: list[str] = []
    parts.append(f"atempo={SPEED}")

    hp = _int_env("AUDIO_HIGHPASS_HZ", 100)
    lp = _int_env("AUDIO_LOWPASS_HZ", 12000)
    if hp > 0:
        parts.append(f"highpass=f={hp}")
    if lp > 0:
        parts.append(f"lowpass=f={lp}")

    denoise = os.environ.get("AUDIO_DENOISE", "0").strip().lower()
    if denoise in ("1", "true", "yes"):
        denoise = "afftdn"
    if denoise == "afftdn":
        # Conservative spectral denoise; tune via env if needed later.
        parts.append("afftdn=nf=-25")
    elif denoise == "arnndn":
        model = os.environ.get("ARNNDN_MODEL_PATH", "").strip()
        if model:
            parts.append(f"arnndn=m='{model}'")
        else:
            # Fall back to afftdn if model is not provided.
            parts.append("afftdn=nf=-25")

    if _truthy_env("AUDIO_COMPRESS", "1"):
        # Gentle compression to keep speech present without pumping.
        parts.append("acompressor=threshold=-18dB:ratio=3:attack=5:release=120:makeup=3")
        parts.append("alimiter=limit=0.95")

    if _truthy_env("AUDIO_LOUDNORM", "0"):
        # Social-friendly target; keep conservative to avoid artifacts.
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
    merged = project_dir / "merged" / "merged.mp4"
    if not merged.exists():
        raise FileNotFoundError(f"Merged video not found: {merged}")

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
                "-af", _audio_filter_chain(),
                "-c:v", "libx264", "-c:a", "aac",
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
                "-af", _audio_filter_chain(),
                "-c:v", "libx264", "-c:a", "aac",
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
        return out_path

    # Determine which aroll segments get zoomed (alternating, starting with no-zoom)
    aroll_segments = [s for s in timeline if s.get("type") == "aroll"]
    zoom_ranges = set()
    for i, seg in enumerate(aroll_segments):
        if i % 2 == 1:
            zoom_ranges.add((float(seg["start"]), float(seg["end"])))

    inputs = ["-i", str(merged)]
    for ov in overlays:
        inputs.extend(["-i", str(ov["clip"])])

    scale_filter = _video_scale_filter(width, height)
    zoom_crop_w = int(width / 1.1)
    zoom_crop_h = int(height / 1.1)
    zoom_x = (width - zoom_crop_w) // 2
    zoom_y = (height - zoom_crop_h) // 2

    filter_parts = []

    # Step 1: Speed up merged video + scale
    filter_parts.append(
        f"[0:v]setpts=PTS/{SPEED},{scale_filter}[scaled]"
    )

    # Step 2: White flash at start
    filter_parts.append(
        f"[scaled]fade=type=in:start_time=0:duration={FLASH_DURATION}:color=white[flashed]"
    )

    # Step 3: Apply zoom on alternating aroll segments
    if zoom_ranges:
        zoom_enable = "+".join(
            f"between(t,{s/SPEED:.3f},{e/SPEED:.3f})"
            for s, e in sorted(zoom_ranges)
        )
        filter_parts.append(
            f"[flashed]crop=w='if({zoom_enable},{zoom_crop_w},{width})':"
            f"h='if({zoom_enable},{zoom_crop_h},{height})':"
            f"x='if({zoom_enable},{zoom_x},0)':"
            f"y='if({zoom_enable},{zoom_y},0)',"
            f"scale={width}:{height}[base]"
        )
    else:
        filter_parts.append("[flashed]copy[base]")

    # Step 4: Scale, trim, fade, and time-align each broll overlay.
    # Use tpad to insert black padding so overlay frames arrive at the correct
    # timestamp instead of being consumed early by the overlay filter.
    for i, ov in enumerate(overlays):
        idx = i + 1
        sped_duration = ov["duration"] / SPEED
        sped_start = ov["start"] / SPEED
        sped_end = ov["end"] / SPEED
        fade_d = min(FADE_DURATION, sped_duration / 3)
        fade_out_start = max(0, sped_duration - fade_d)
        filter_parts.append(
            f"[{idx}:v]trim=0:{sped_duration},setpts=PTS-STARTPTS,"
            f"{scale_filter},"
            f"fade=type=in:start_time=0:duration={fade_d}:color=black,"
            f"fade=type=out:start_time={fade_out_start:.3f}:duration={fade_d}:color=black,"
            f"tpad=start_duration={sped_start:.3f}:color=black"
            f"[ov{i}]"
        )

    # Step 5: Overlay broll -- tpad ensures frames are time-aligned;
    # enable controls visibility so black padding isn't shown.
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
            total_real_end = max(float(ov["end"]) for ov in overlays)
            total_sped_d = (total_real_end / SPEED) + flash_d + 0.1
            flash_src = "flashsrc"
            filter_parts.append(
                f"color=c=white:s={width}x{height}:r=30:d={total_sped_d:.3f}[{flash_src}]"
            )

            for j, (fs, fe) in enumerate(flash_windows):
                out_label = f"flashout{j}"
                filter_parts.append(
                    f"[{current}][{flash_src}]overlay=0:0:"
                    f"enable='between(t,{fs:.3f},{fe:.3f})':"
                    f"eof_action=pass[{out_label}]"
                )
                current = out_label

    # Step 5b: Optional burn-in captions (safe-zone only)
    if _truthy_env("BURN_IN_CAPTIONS", "1"):
        captions_path = project_dir / "subtitles" / "captions.srt"
        if captions_path.exists():
            try:
                margin_v = int(float(os.environ.get("CAPTION_MARGIN_V", "80")))
            except ValueError:
                margin_v = 80
            try:
                font_size = int(float(os.environ.get("CAPTION_FONT_SIZE", "54")))
            except ValueError:
                font_size = 54

            # Windows FFmpeg sometimes has issues with backslashes in filter args.
            cap_path = str(captions_path).replace("\\", "/")
            # FFmpeg subtitles filter parses ':' as an option separator (e.g. original_size).
            # So we must escape the Windows drive colon: C:/... -> C\:/...
            if len(cap_path) >= 2 and cap_path[1] == ":":
                cap_path = cap_path[0] + "\\:" + cap_path[2:]
            style = (
                "Alignment=2,"  # bottom-center
                f"MarginV={margin_v},"
                f"Fontsize={font_size},"
                "Outline=3,"
                "BorderStyle=3,"
                "Shadow=0,"
                "BackColour=&H80000000"  # semi-transparent black
            )
            cap_out = "capout"
            filter_parts.append(
                f"[{current}]subtitles='{cap_path}':charenc=UTF-8:force_style='{style}'[{cap_out}]"
            )
            current = cap_out

    # Step 6: Speaker audio chain (denoise/EQ/dynamics/loudness/gain) + optional filler mutes
    filler_segs = [ov for ov in overlays if ov["mute"]]
    if filler_segs:
        mute_expr = ",".join(
            f"volume=enable='between(t,{f['start']/SPEED:.3f},{f['end']/SPEED:.3f})':volume=0"
            for f in filler_segs
        )
        filter_parts.append(f"[0:a]{_audio_filter_chain(mute_expr=mute_expr)}[aout]")
        audio_map = "[aout]"
    else:
        filter_parts.append(f"[0:a]{_audio_filter_chain()}[aout]")
        audio_map = "[aout]"

    filter_complex = ";\n".join(filter_parts)
    if _truthy_env("RENDER_DEBUG_FILTERS", "0"):
        print(f"[DEBUG] filter_complex:\n{filter_complex}")

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{current}]",
        "-map", audio_map,
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        str(out_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[DEBUG] FFmpeg stderr:\n{result.stderr[-2000:]}")
        result.check_returncode()

    return out_path
