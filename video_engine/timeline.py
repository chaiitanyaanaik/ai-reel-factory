"""
Align editing plan to real transcript. Optional filler segments (um/ah) if USE_FILLER_BROLL=1.
Produces timeline: A-roll vs B-roll; B-roll duration capped (~5s default).
"""
from pathlib import Path
import json
import os

# Max real-time seconds each B-roll overlay is shown (trimmed on timeline; Veo clips match).
_DEFAULT_MAX_BROLL = 5.0


def load_transcript(project_dir: Path) -> dict:
    path = project_dir / "transcripts" / "transcript.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_plan(project_dir: Path) -> dict:
    path = project_dir / "final_script.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_filler_segments(project_dir: Path) -> list[dict]:
    path = project_dir / "cuts" / "filler_segments.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def align_plan_to_transcript(
    transcript: dict,
    plan: dict,
) -> list[dict]:
    """
    Scale editing plan beats to actual transcript duration.
    Each entry: {start, end, type, suggestion?, broll_index?}.
    """
    segments = transcript.get("segments") or []
    beats = plan.get("beats") or []
    plan_total = max(1, float(plan.get("total_duration_seconds", 30)))
    # If no transcript segments (e.g. silent or very short), use plan duration
    actual_total = float(segments[-1]["end"]) if segments else plan_total

    scale = actual_total / plan_total
    timeline = []
    for i, b in enumerate(beats):
        s = float(b.get("start", 0)) * scale
        e = float(b.get("end") or b.get("end_seconds", 0)) * scale
        t = b.get("type", "aroll")
        timeline.append({
            "start": s,
            "end": e,
            "type": t,
            "suggestion": b.get("suggestion", ""),
            "broll_index": i + 1 if t == "broll" else None,
        })
    # If plan is shorter than actual video, extend last beat to end
    if timeline and timeline[-1]["end"] < actual_total:
        timeline[-1]["end"] = actual_total
    return timeline


def _split_by_fillers(
    timeline: list[dict],
    filler_ranges: list[dict],
) -> list[dict]:
    """
    Insert filler segments into the timeline. Where a filler overlaps an aroll segment,
    split aroll into [aroll, filler, aroll]. Filler segments show B-roll + mute.
    """
    if not filler_ranges:
        return timeline
    filler_ranges = sorted(filler_ranges, key=lambda x: (x["start"], x["end"]))
    out = []
    for seg in timeline:
        if seg["type"] != "aroll":
            out.append(seg)
            continue
        s, e = seg["start"], seg["end"]
        # Find fillers that overlap this segment
        overlapping = [f for f in filler_ranges if f["end"] > s and f["start"] < e]
        if not overlapping:
            out.append(seg)
            continue
        # Split aroll into pieces, inserting filler between
        t = s
        for f in overlapping:
            fs, fe = f["start"], f["end"]
            if t < fs:
                out.append({"start": t, "end": fs, "type": "aroll", "suggestion": "", "broll_index": None})
            out.append({"start": fs, "end": fe, "type": "filler", "suggestion": "", "broll_index": "filler"})
            t = fe
        if t < e:
            out.append({"start": t, "end": e, "type": "aroll", "suggestion": "", "broll_index": None})
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def _cap_broll_duration(timeline: list[dict], max_seconds: float) -> list[dict]:
    """Shorten each broll segment to at most max_seconds (rest of that window shows A-roll base)."""
    if max_seconds <= 0:
        return timeline
    out = []
    for seg in timeline:
        if seg.get("type") != "broll":
            out.append(seg)
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        dur = end - start
        if dur <= max_seconds:
            out.append(seg)
        else:
            out.append({**seg, "end": start + max_seconds})
    return out


def build_timeline_with_fillers(
    project_dir: Path,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (timeline, filler_ranges).
    Set USE_FILLER_BROLL=1 to insert um/ah filler overlays (requires filler stage + filler_segments.json).
    Default: no filler inserts. B-roll clips capped by MAX_BROLL_SECONDS (default 5).
    """
    transcript = load_transcript(project_dir)
    plan = load_plan(project_dir)
    use_fillers = os.environ.get("USE_FILLER_BROLL", "0").strip().lower() in ("1", "true", "yes")
    filler_list = load_filler_segments(project_dir) if use_fillers else []
    timeline = align_plan_to_transcript(transcript, plan)
    timeline = _split_by_fillers(timeline, filler_list)
    try:
        max_b = float(os.environ.get("MAX_BROLL_SECONDS", str(_DEFAULT_MAX_BROLL)))
    except ValueError:
        max_b = _DEFAULT_MAX_BROLL
    timeline = _cap_broll_duration(timeline, max_b)
    return timeline, filler_list


def save_timeline(project_dir: Path, timeline: list[dict], filler_ranges: list[dict]) -> Path:
    out_dir = project_dir / "cuts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    data = {"timeline": timeline, "filler_ranges": filler_ranges}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
