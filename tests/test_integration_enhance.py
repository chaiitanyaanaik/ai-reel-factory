"""Integration: merge + enhance with a tiny generated fixture (requires ffmpeg)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not on PATH")


def _make_silent_mp4(path: Path, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=320x240:d={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_merge_and_enhance(tmp_path, monkeypatch):
    import core.project_store as ps

    monkeypatch.setenv("AUDIO_DENOISE", "afftdn")
    monkeypatch.setattr(ps, "PROJECTS_DIR", tmp_path)
    from schemas.models import PipelineMode
    from video_engine.enhance import enhance_audio, social_audio_filters
    from video_engine.merge_clips import merge_clips

    m = ps.create_project(name="itest", mode=PipelineMode.video_first)
    project_dir = tmp_path / m.id
    clip = project_dir / "raw_clips" / "01.mp4"
    _make_silent_mp4(clip, 0.5)

    merged = merge_clips(project_dir)
    assert merged.exists()
    assert social_audio_filters()

    enhanced = enhance_audio(project_dir)
    assert enhanced.exists()
    assert enhanced.stat().st_size > 0
