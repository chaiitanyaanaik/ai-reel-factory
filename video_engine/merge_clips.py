"""
Merge raw clips into a single file (merged/merged.mp4).
Uses FFmpeg concat demuxer for lossless concatenation.
"""
from pathlib import Path
import shutil
import subprocess


def _check_ffmpeg() -> None:
    """Raise a clear error if ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "FFmpeg not found. Install it and add it to your PATH.\n"
            "  Windows: winget install FFmpeg\n"
            "  Or download from https://ffmpeg.org/download.html\n"
            "  Then restart your terminal."
        )


def get_raw_clips(project_dir: Path) -> list[Path]:
    raw = project_dir / "raw_clips"
    if not raw.exists():
        return []
    # Sort by name so order is deterministic
    files = sorted(raw.glob("*.mp4")) + sorted(raw.glob("*.mov"))
    return files


def merge_clips(project_dir: Path) -> Path:
    _check_ffmpeg()
    clips = get_raw_clips(project_dir)
    if not clips:
        raise FileNotFoundError(f"No clips in {project_dir / 'raw_clips'}")

    merged_dir = project_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    out_path = merged_dir / "merged.mp4"

    # Concat demuxer: list file with one line per file
    list_file = merged_dir / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve().as_posix()}'" for c in clips),
        encoding="utf-8",
    )

    # -f concat -safe 0: use concat demuxer, allow absolute paths
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path
