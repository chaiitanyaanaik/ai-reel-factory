"""
Detect filler words (um, uh, like, you know) from Whisper transcript.
Output: list of (start, end) segments to cover with B-roll so video stays in sync.
"""
from pathlib import Path

FILLER_WORDS = {"um", "uh", "like", "you know", "actually", "basically"}


def load_transcript(project_dir: Path) -> dict:
    import json
    path = project_dir / "transcripts" / "transcript.json"
    return json.loads(path.read_text(encoding="utf-8"))


def get_filler_segments(transcript: dict) -> list[tuple[float, float]]:
    """
    Return list of (start_sec, end_sec) for each filler word/phrase.
    Uses segments when word_timestamps not available; otherwise use word-level.
    """
    segments = transcript.get("segments") or []
    filler_ranges = []

    for seg in segments:
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = (seg.get("text") or "").strip().lower()

        # Word-level timestamps (if Whisper provided them)
        words = seg.get("words") or []
        if words:
            for w in words:
                word = (w.get("word") or "").strip().lower()
                if word in FILLER_WORDS or "you know" in text and word in ("you", "know"):
                    ws = float(w.get("start", start))
                    we = float(w.get("end", end))
                    filler_ranges.append((ws, we))
            continue

        # Segment-level: check if whole segment is filler
        for filler in FILLER_WORDS:
            if filler in text:
                filler_ranges.append((start, end))
                break

    # Merge adjacent/overlapping segments so "you" + "know" -> one segment
    merged = []
    for s, e in sorted(filler_ranges):
        if merged and s <= merged[-1][1] + 0.15:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def save_filler_segments(project_dir: Path, segments: list[tuple[float, float]]) -> Path:
    import json
    out_dir = project_dir / "cuts"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = [{"start": s, "end": e} for s, e in segments]
    path = out_dir / "filler_segments.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
