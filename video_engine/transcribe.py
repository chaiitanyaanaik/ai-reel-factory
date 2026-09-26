"""
Transcribe merged video with Whisper. Output word-level timestamps for filler detection.
"""
from pathlib import Path


def resolve_video_for_transcribe(project_dir: Path) -> Path:
    """Prefer enhanced audio merge when available."""
    from .enhance import resolve_source_video

    return resolve_source_video(project_dir)


def transcribe(video_path: Path, model_size: str = "base") -> dict:
    """
    Return Whisper result dict with 'segments' (and optionally word-level).
    Each segment: {"start": float, "end": float, "text": str}
    """
    try:
        import whisper
    except ImportError:
        raise ImportError("Install openai-whisper: pip install openai-whisper")

    model = whisper.load_model(model_size)
    result = model.transcribe(
        str(video_path),
        word_timestamps=True,
        verbose=False,
    )
    return result


def save_transcript(project_dir: Path, data: dict) -> Path:
    import json
    out_dir = project_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Whisper result can contain numpy; make JSON-serializable
    def sanitize(obj):
        if hasattr(obj, "item"):
            return float(obj)
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(x) for x in obj]
        return obj
    out_path = out_dir / "transcript.json"
    out_path.write_text(json.dumps(sanitize(data), indent=2), encoding="utf-8")
    return out_path
