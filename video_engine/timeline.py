"""
Align editing plan to real transcript.
Prefers word/segment-anchored beat times; falls back to duration scaling.
Optional filler inserts only if USE_FILLER_BROLL=1 (deprecated product path).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

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


def _flatten_words(transcript: dict) -> list[dict]:
    """List of {word, start, end} from Whisper word timestamps."""
    words: list[dict] = []
    for seg in transcript.get("segments") or []:
        for w in seg.get("words") or []:
            token = (w.get("word") or w.get("text") or "").strip()
            if not token:
                continue
            words.append(
                {
                    "word": token,
                    "start": float(w.get("start", seg.get("start", 0))),
                    "end": float(w.get("end", seg.get("end", 0))),
                }
            )
    return words


def _beat_times(beat: dict, words: list[dict]) -> tuple[float, float] | None:
    """Resolve absolute start/end from anchors if present."""
    if beat.get("start_time") is not None and (
        beat.get("end_time") is not None
        or beat.get("end_seconds") is not None
        or beat.get("end") is not None
    ):
        s = float(beat["start_time"])
        e = float(
            beat.get("end_time")
            if beat.get("end_time") is not None
            else (beat.get("end_seconds") or beat.get("end") or s)
        )
        return s, e

    sw = beat.get("start_word_index")
    ew = beat.get("end_word_index")
    if words and sw is not None and ew is not None:
        try:
            si, ei = int(sw), int(ew)
            if 0 <= si < len(words) and 0 <= ei < len(words):
                return float(words[si]["start"]), float(words[ei]["end"])
        except (TypeError, ValueError):
            pass
    return None


def align_plan_to_transcript(
    transcript: dict,
    plan: dict,
) -> list[dict]:
    """
    Align beats to transcript. Prefer start_time/end_time or word indices;
    otherwise scale plan times to actual duration.
    """
    segments = transcript.get("segments") or []
    beats = plan.get("beats") or []
    plan_total = max(1.0, float(plan.get("total_duration_seconds", 30)))
    actual_total = float(segments[-1]["end"]) if segments else plan_total
    words = _flatten_words(transcript)

    anchored = all(_beat_times(b, words) is not None for b in beats) if beats else False
    scale = 1.0 if anchored else (actual_total / plan_total)

    timeline: list[dict] = []
    broll_n = 0
    for i, b in enumerate(beats):
        times = _beat_times(b, words)
        if times is not None and (anchored or b.get("start_time") is not None):
            s, e = times
        else:
            s = float(b.get("start", 0)) * scale
            e = float(b.get("end") or b.get("end_seconds") or 0) * scale
        t = b.get("type", "aroll")
        idx = None
        if t == "broll":
            broll_n += 1
            idx = broll_n
        timeline.append(
            {
                "start": max(0.0, s),
                "end": max(s, e),
                "type": t,
                "suggestion": b.get("suggestion", ""),
                "broll_index": idx,
            }
        )

    if timeline and timeline[-1]["end"] < actual_total:
        timeline[-1]["end"] = actual_total
    return timeline


def _split_by_fillers(
    timeline: list[dict],
    filler_ranges: list[dict],
) -> list[dict]:
    """Deprecated path: insert filler mute overlays into aroll windows."""
    if not filler_ranges:
        return timeline
    filler_ranges = sorted(filler_ranges, key=lambda x: (x["start"], x["end"]))
    out = []
    for seg in timeline:
        if seg["type"] != "aroll":
            out.append(seg)
            continue
        s, e = seg["start"], seg["end"]
        overlapping = [f for f in filler_ranges if f["end"] > s and f["start"] < e]
        if not overlapping:
            out.append(seg)
            continue
        t = s
        for f in overlapping:
            fs, fe = f["start"], f["end"]
            if t < fs:
                out.append(
                    {
                        "start": t,
                        "end": fs,
                        "type": "aroll",
                        "suggestion": "",
                        "broll_index": None,
                    }
                )
            out.append(
                {
                    "start": fs,
                    "end": fe,
                    "type": "filler",
                    "suggestion": "",
                    "broll_index": "filler",
                }
            )
            t = fe
        if t < e:
            out.append(
                {
                    "start": t,
                    "end": e,
                    "type": "aroll",
                    "suggestion": "",
                    "broll_index": None,
                }
            )
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def _cap_broll_duration(timeline: list[dict], max_seconds: float) -> list[dict]:
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
    transcript = load_transcript(project_dir)
    plan = load_plan(project_dir)
    use_fillers = os.environ.get("USE_FILLER_BROLL", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    filler_list = load_filler_segments(project_dir) if use_fillers else []
    timeline = align_plan_to_transcript(transcript, plan)
    timeline = _split_by_fillers(timeline, filler_list)
    # Prefer plan/editor duration; safety ceiling from EDITOR_MAX_BROLL_SECONDS or MAX_BROLL_SECONDS
    try:
        from script_engine.editor_agent import load_editor_rules

        max_b = load_editor_rules().max_broll_seconds
    except Exception:
        max_b = _DEFAULT_MAX_BROLL
    try:
        env_cap = os.environ.get("MAX_BROLL_SECONDS", "").strip()
        if env_cap:
            max_b = float(env_cap)
    except ValueError:
        pass
    timeline = _cap_broll_duration(timeline, max_b)
    return timeline, filler_list


def save_timeline(project_dir: Path, timeline: list[dict], filler_ranges: list[dict]) -> Path:
    out_dir = project_dir / "cuts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "timeline.json"
    data = {"timeline": timeline, "filler_ranges": filler_ranges}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
