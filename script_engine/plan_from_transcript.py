"""
Plan editing beats from an existing Whisper transcript (video-first path).

Uses the editor agent (prompt + programmatic guardrails).
Lip-sync invariant: cleaned script must keep the same spoken words.
"""
from __future__ import annotations

import json
from pathlib import Path

from .editing_plan import _enforce_aroll_start, save_editing_plan
from .editor_agent import (
    apply_editor_guardrails,
    build_editor_prompt,
    load_editor_rules,
)
from .lip_sync import assert_lip_sync_safe, transcript_plain_text
from .llm import gemini_json, load_references, style_guide_text


def _load_transcript(project_dir: Path) -> dict:
    path = project_dir / "transcripts" / "transcript.json"
    if not path.exists():
        raise FileNotFoundError(f"Run transcribe first: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _optional_topic(project_dir: Path) -> str:
    path = project_dir / "topic.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _segment_summary(transcript: dict, max_segments: int = 80) -> str:
    lines = []
    for i, seg in enumerate(transcript.get("segments") or []):
        if i >= max_segments:
            lines.append("... (truncated)")
            break
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = (seg.get("text") or "").strip()
        lines.append(f"[{start:.2f}-{end:.2f}] {text}")
    return "\n".join(lines)


def plan_from_transcript(project_dir: Path) -> dict:
    """
    Editor agent: cleaned_script + A/B-roll plan with pacing guardrails.
    Fails hard if dialogue is rewritten (unless ALLOW_PLACEHOLDERS).
    """
    from core.config import allow_placeholders

    transcript = _load_transcript(project_dir)
    plain = transcript_plain_text(transcript)
    if not plain:
        raise ValueError("Transcript is empty; cannot build editing plan")

    segments = transcript.get("segments") or []
    total = float(segments[-1]["end"]) if segments else 30.0
    topic = _optional_topic(project_dir)
    refs = load_references("visual-patterns", "writing-styles")
    style = style_guide_text(project_dir)
    rules = load_editor_rules()

    prompt = build_editor_prompt(
        plain=plain,
        segment_summary=_segment_summary(transcript),
        topic=topic,
        refs=refs,
        style=style,
        total_duration=total,
        rules=rules,
    )

    try:
        plan = gemini_json(prompt)
    except Exception as e:
        if allow_placeholders():
            print(f"[WARN] plan_from_transcript failed ({e}), using all-aroll fallback")
            return _fallback_from_transcript(transcript, plain)
        raise RuntimeError(f"plan_from_transcript failed: {e}") from e

    cleaned = (plan.get("cleaned_script") or "").strip()
    if not cleaned:
        cleaned = plain
        plan["cleaned_script"] = cleaned

    try:
        ratio = assert_lip_sync_safe(plain, cleaned)
        print(f"[OK] Lip-sync guard passed (overlap {ratio:.1%})")
    except ValueError:
        if allow_placeholders():
            print("[WARN] Lip-sync guard failed; keeping verbatim transcript as script")
            cleaned = plain
            plan["cleaned_script"] = cleaned
        else:
            raise

    if "beats" not in plan or not isinstance(plan["beats"], list):
        raise ValueError("LLM response missing 'beats' list")

    plan["total_duration_seconds"] = float(plan.get("total_duration_seconds") or total)
    _enforce_aroll_start(plan)
    plan = apply_editor_guardrails(plan, total_duration=total, rules=rules)
    return plan


def save_cleaned_script(project_dir: Path, cleaned: str) -> Path:
    path = project_dir / "raw_script.md"
    path.write_text(cleaned.strip() + "\n", encoding="utf-8")
    return path


def run_plan_stage(project_dir: Path) -> dict:
    plan = plan_from_transcript(project_dir)
    cleaned = plan.get("cleaned_script") or transcript_plain_text(_load_transcript(project_dir))
    save_cleaned_script(project_dir, cleaned)
    save_editing_plan(project_dir, plan)
    n_broll = sum(1 for b in plan.get("beats") or [] if b.get("type") == "broll")
    print(f"[OK] Editor plan + cleaned script ({n_broll} B-rolls)")
    return plan


def _fallback_from_transcript(transcript: dict, plain: str) -> dict:
    segments = transcript.get("segments") or []
    total = float(segments[-1]["end"]) if segments else 30.0
    plan = {
        "cleaned_script": plain,
        "total_duration_seconds": total,
        "hook": {"start": 0, "end_seconds": min(4, total), "note": "speaker hook"},
        "beats": [
            {
                "type": "aroll",
                "start": 0,
                "start_time": 0,
                "end_seconds": total,
                "end_time": total,
                "suggestion": "",
            }
        ],
    }
    return apply_editor_guardrails(plan, total_duration=total)
