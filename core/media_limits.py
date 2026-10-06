"""Upload duration limits + ffprobe helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.config import env_int


def max_clip_duration_seconds() -> int:
    """Max length of a single uploaded clip (default 180)."""
    return max(1, env_int("MAX_CLIP_DURATION_SECONDS", 180))


def max_project_duration_seconds() -> int:
    """Max total length of all clips in a project (default 180)."""
    return max(1, env_int("MAX_PROJECT_DURATION_SECONDS", 180))


def probe_duration_seconds(path: Path) -> float:
    """
    Return media duration in seconds via ffprobe.
    Raises RuntimeError if unreadable / missing duration.
    """
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"File not found: {path.name}")
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe is not installed on the server") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Timed out reading duration for {path.name}") from e

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err or f"Could not read duration for {path.name}")

    raw = (result.stdout or "").strip()
    try:
        duration = float(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid duration for {path.name}: {raw!r}") from e
    if duration <= 0 or duration != duration:  # NaN
        raise RuntimeError(f"Invalid duration for {path.name}")
    return duration


def format_duration_label(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    m, s = divmod(total, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
