"""
Generate SRT subtitles from transcript. Optional: strip filler words from caption text.
"""
from pathlib import Path


def _wrap_caption(
    text: str,
    max_total_chars: int = 48,
    max_lines: int = 2,
    max_line_chars: int = 26,
) -> str:
    """
    Keep captions short to reduce overlap with faces/object.
    Deterministic: word truncation + greedy 1-2 line wrapping.
    """
    text = " ".join((text or "").strip().split())
    if not text:
        return ""

    words = text.split()
    kept: list[str] = []
    total = 0
    for w in words:
        add_len = len(w) if not kept else (1 + len(w))
        if total + add_len > max_total_chars:
            break
        kept.append(w)
        total += add_len

    lines: list[str] = []
    cur = ""
    for w in kept:
        if not cur:
            cur = w
            continue
        if len(cur) + 1 + len(w) <= max_line_chars:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            if len(lines) >= max_lines:
                break
            cur = w

    if cur and len(lines) < max_lines:
        lines.append(cur)

    return "\n".join(lines[:max_lines]).strip()


def load_transcript(project_dir: Path) -> dict:
    import json
    path = project_dir / "transcripts" / "transcript.json"
    return json.loads(path.read_text(encoding="utf-8"))


def segments_to_srt(segments: list[dict], strip_fillers: bool = True) -> str:
    from .filler_detection import FILLER_WORDS

    def to_srt_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = (seg.get("text") or "").strip()
        if strip_fillers:
            words = text.lower().split()
            text = " ".join(w for w in words if w not in FILLER_WORDS and w != "you" and w != "know").strip()
            # Fix "you know" as phrase
            if "you know" in (seg.get("text") or "").lower():
                text = text.replace("you know", "").strip()
        if not text:
            continue

        text = _wrap_caption(text)
        if not text:
            continue

        lines.append(f"{i}\n{to_srt_time(start)} --> {to_srt_time(end)}\n{text}\n")
    return "\n".join(lines)


def generate_srt(project_dir: Path, strip_fillers: bool = True) -> Path:
    data = load_transcript(project_dir)
    segments = data.get("segments") or []
    srt_content = segments_to_srt(segments, strip_fillers=strip_fillers)
    out_dir = project_dir / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "captions.srt"
    path.write_text(srt_content, encoding="utf-8")
    return path
