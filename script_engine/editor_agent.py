"""
Editor agent: plans A-roll / B-roll from a transcript with hard pacing guardrails.

Prompt rules + programmatic validate/repair (LLM alone is not trusted for counts/gaps).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.config import env_float, env_int, load_env


@dataclass(frozen=True)
class EditorRules:
    """Pacing guardrails for short-form talking-head Reels."""

    max_broll_count: int = 5
    min_broll_seconds: float = 2.0
    max_broll_seconds: float = 5.0
    min_gap_between_brolls: float = 3.5  # A-roll (or silence) between B-roll windows
    min_hook_aroll_seconds: float = 3.0
    min_end_aroll_seconds: float = 2.5
    min_aroll_share: float = 0.55  # >=55% of runtime should be face/A-roll


def load_editor_rules() -> EditorRules:
    load_env()
    return EditorRules(
        max_broll_count=env_int("EDITOR_MAX_BROLL_COUNT", 5),
        min_broll_seconds=env_float("EDITOR_MIN_BROLL_SECONDS", 2.0),
        max_broll_seconds=env_float("EDITOR_MAX_BROLL_SECONDS", 5.0),
        min_gap_between_brolls=env_float("EDITOR_MIN_BROLL_GAP", 3.5),
        min_hook_aroll_seconds=env_float("EDITOR_MIN_HOOK_AROLL", 3.0),
        min_end_aroll_seconds=env_float("EDITOR_MIN_END_AROLL", 2.5),
        min_aroll_share=env_float("EDITOR_MIN_AROLL_SHARE", 0.55),
    )


def build_editor_prompt(
    *,
    plain: str,
    segment_summary: str,
    topic: str,
    refs: str,
    style: str,
    total_duration: float,
    rules: EditorRules,
) -> str:
    return f"""You are a senior Instagram Reel EDITOR (not a copywriter).
You receive a VERBATIM speech transcript with times.
Plan an edit: when we see the SPEAKER (aroll) vs B-roll cutaways.
Do NOT change spoken words.

## Lip-sync (absolute)
- cleaned_script MUST use the same words as the transcript (punctuation/line breaks OK).
- Do NOT paraphrase, shorten, invent, or improve dialogue.
- Beat start_time/end_time MUST fall on the transcript timeline (0 .. {total_duration:.2f}s).

## Editor craft (where B-roll belongs)
- B-roll = visual PROOF for a concrete claim (action, object, place, emotion you can SHOW).
- Prefer: after the hook; on concrete nouns; on list/steps; not on the final punchline.
- Avoid: abstract fluff stock; mid-hook; back-to-back B-rolls; covering the CTA on face.

## B-roll suggestion craft (critical for Veo)
- Format: "[Subject doing a physical action]. Camera: [one move]. [Scene/lighting]."
- MUST be literal and filmable: body language, hands, posture, eyes looking down, a nod, a flinch.
- Ban emotion adjectives (apprehension, fear, hope, tension, obedience) — show them as actions.
- Setting: posh contemporary Indian home; casting: Indian parent + child, elevated casual wardrobe.
- Do NOT invent astrology charts, zodiac wheels, or mystical symbols unless the spoken line in that window explicitly asks for them.
- Ground each suggestion in the spoken words of THAT time window (timed segments), not a generic theme.
- Keep casting consistent across beats (same age band / household look when people appear).

## Hard pacing guardrails (must obey)
- At most {rules.max_broll_count} B-roll beats in the whole video.
- Each B-roll duration between {rules.min_broll_seconds:.1f}s and {rules.max_broll_seconds:.1f}s.
- At least {rules.min_gap_between_brolls:.1f}s of A-roll between any two B-roll windows.
- First {rules.min_hook_aroll_seconds:.1f}s MUST be A-roll (face hook).
- Last {rules.min_end_aroll_seconds:.1f}s MUST be A-roll (close on face).
- A-roll must cover at least {rules.min_aroll_share:.0%} of total runtime.
- Pattern: face → cutaway → face → cutaway (never wall-to-wall B-roll).

## Optional intent (NOT spoken dialogue — B-roll theme only)
{topic or "(none)"}

## Transcript (verbatim)
{plain}

## Timed segments
{segment_summary}

{f"## Reference Material\\n{refs}" if refs else ""}

{style}

## Output JSON only
{{
  "cleaned_script": "markdown with punctuation, SAME WORDS",
  "total_duration_seconds": {total_duration:.2f},
  "editor_notes": "1-3 sentences on why you placed B-rolls",
  "hook": {{"start": 0, "end_seconds": <float>, "note": "speaker face-to-camera"}},
  "beats": [
    {{
      "type": "aroll" | "broll",
      "start_time": <float>,
      "end_time": <float>,
      "start": <same as start_time>,
      "end_seconds": <same as end_time>,
      "suggestion": "" for aroll; for broll: "[Subject physical action]. Camera: [one move]. [Scene]."
    }}
  ]
}}

Beats must cover the full timeline without large gaps. Prefer fewer strong B-rolls over many weak ones.
B-roll suggestions must be concrete physical actions matching the spoken line in that window.
"""


def _norm_beat(beat: dict) -> dict:
    st = float(beat.get("start_time", beat.get("start", 0)) or 0)
    et = float(
        beat.get("end_time")
        if beat.get("end_time") is not None
        else (beat.get("end_seconds") or beat.get("end") or st)
    )
    if et < st:
        et = st
    out = dict(beat)
    out["type"] = "broll" if out.get("type") == "broll" else "aroll"
    out["start"] = st
    out["start_time"] = st
    out["end_seconds"] = et
    out["end_time"] = et
    out["suggestion"] = out.get("suggestion") or ""
    return out


def normalize_beats(plan: dict, total_duration: float) -> list[dict]:
    beats = [_norm_beat(b) for b in (plan.get("beats") or [])]
    beats.sort(key=lambda b: (b["start_time"], b["end_time"]))
    if not beats:
        return [
            {
                "type": "aroll",
                "start": 0.0,
                "start_time": 0.0,
                "end_seconds": total_duration,
                "end_time": total_duration,
                "suggestion": "",
            }
        ]
    # Clamp into [0, total]
    for b in beats:
        b["start_time"] = max(0.0, min(b["start_time"], total_duration))
        b["end_time"] = max(b["start_time"], min(b["end_time"], total_duration))
        b["start"] = b["start_time"]
        b["end_seconds"] = b["end_time"]
    return beats


def _broll_indices(beats: list[dict]) -> list[int]:
    return [i for i, b in enumerate(beats) if b.get("type") == "broll"]


def _to_aroll(beat: dict) -> dict:
    b = dict(beat)
    b["type"] = "aroll"
    b["suggestion"] = ""
    return b


def validate_editor_plan(
    plan: dict,
    total_duration: float,
    rules: EditorRules | None = None,
) -> list[str]:
    """Return list of violation messages (empty = OK)."""
    rules = rules or load_editor_rules()
    beats = normalize_beats(plan, total_duration)
    issues: list[str] = []

    if not beats:
        return ["no beats"]

    if beats[0]["type"] != "aroll":
        issues.append("first beat must be aroll (hook)")
    elif beats[0]["end_time"] < rules.min_hook_aroll_seconds:
        # Allow if first aroll ends early but another aroll covers hook — check coverage
        covered = _aroll_coverage_until(beats, rules.min_hook_aroll_seconds)
        if covered < rules.min_hook_aroll_seconds - 0.05:
            issues.append(
                f"hook needs >={rules.min_hook_aroll_seconds}s aroll at start "
                f"(have {covered:.1f}s)"
            )

    end_aroll = _aroll_coverage_from(beats, max(0.0, total_duration - rules.min_end_aroll_seconds))
    if end_aroll < rules.min_end_aroll_seconds - 0.05:
        issues.append(
            f"ending needs >={rules.min_end_aroll_seconds}s aroll "
            f"(have {end_aroll:.1f}s)"
        )

    brolls = [b for b in beats if b["type"] == "broll"]
    if len(brolls) > rules.max_broll_count:
        issues.append(f"too many brolls: {len(brolls)} > {rules.max_broll_count}")

    for b in brolls:
        dur = b["end_time"] - b["start_time"]
        if dur > rules.max_broll_seconds + 0.05:
            issues.append(f"broll too long ({dur:.1f}s > {rules.max_broll_seconds}s) @ {b['start_time']:.1f}")
        if dur < rules.min_broll_seconds - 0.05:
            issues.append(f"broll too short ({dur:.1f}s < {rules.min_broll_seconds}s) @ {b['start_time']:.1f}")

    for i in range(len(brolls) - 1):
        gap = brolls[i + 1]["start_time"] - brolls[i]["end_time"]
        if gap < rules.min_gap_between_brolls - 0.05:
            issues.append(
                f"brolls too close: gap {gap:.1f}s < {rules.min_gap_between_brolls}s "
                f"({brolls[i]['end_time']:.1f} → {brolls[i+1]['start_time']:.1f})"
            )

    aroll_dur = sum(
        (b["end_time"] - b["start_time"]) for b in beats if b["type"] == "aroll"
    )
    share = aroll_dur / max(total_duration, 0.01)
    if share < rules.min_aroll_share - 0.01:
        issues.append(f"aroll share {share:.0%} < {rules.min_aroll_share:.0%}")

    return issues


def _aroll_coverage_until(beats: list[dict], until: float) -> float:
    covered = 0.0
    for b in beats:
        if b["type"] != "aroll":
            continue
        s = max(0.0, b["start_time"])
        e = min(until, b["end_time"])
        if e > s:
            covered += e - s
    return covered


def _aroll_coverage_from(beats: list[dict], start: float) -> float:
    covered = 0.0
    for b in beats:
        if b["type"] != "aroll":
            continue
        s = max(start, b["start_time"])
        e = b["end_time"]
        if e > s:
            covered += e - s
    return covered


def repair_editor_plan(
    plan: dict,
    total_duration: float,
    rules: EditorRules | None = None,
) -> tuple[dict, list[str]]:
    """
    Deterministically repair pacing violations.
    Returns (plan, list of repair actions taken).
    """
    rules = rules or load_editor_rules()
    actions: list[str] = []
    beats = normalize_beats(plan, total_duration)

    # 1) Trim / drop too-short or too-long B-rolls
    fixed: list[dict] = []
    for b in beats:
        if b["type"] != "broll":
            fixed.append(b)
            continue
        dur = b["end_time"] - b["start_time"]
        if dur < rules.min_broll_seconds:
            actions.append(f"drop short broll @ {b['start_time']:.1f}s ({dur:.1f}s)")
            fixed.append(_to_aroll(b))
            continue
        if dur > rules.max_broll_seconds:
            b = dict(b)
            b["end_time"] = b["start_time"] + rules.max_broll_seconds
            b["end_seconds"] = b["end_time"]
            actions.append(
                f"trim broll @ {b['start_time']:.1f}s to {rules.max_broll_seconds}s"
            )
        fixed.append(b)
    beats = fixed

    # 2) Enforce hook aroll at start
    if not beats or beats[0]["type"] != "aroll":
        hook_end = min(rules.min_hook_aroll_seconds, total_duration)
        beats.insert(
            0,
            {
                "type": "aroll",
                "start": 0.0,
                "start_time": 0.0,
                "end_seconds": hook_end,
                "end_time": hook_end,
                "suggestion": "",
            },
        )
        actions.append("insert aroll hook at start")
        if len(beats) > 1 and beats[1]["start_time"] < hook_end:
            beats[1] = dict(beats[1])
            beats[1]["start_time"] = hook_end
            beats[1]["start"] = hook_end
            if beats[1]["end_time"] < hook_end:
                beats[1]["end_time"] = hook_end
                beats[1]["end_seconds"] = hook_end
    elif beats[0]["end_time"] < rules.min_hook_aroll_seconds:
        need = min(rules.min_hook_aroll_seconds, total_duration)
        beats[0] = dict(beats[0])
        beats[0]["end_time"] = need
        beats[0]["end_seconds"] = need
        actions.append(f"extend hook aroll to {need:.1f}s")
        # Push following beat if overlapping
        if len(beats) > 1 and beats[1]["start_time"] < need:
            beats[1] = dict(beats[1])
            if beats[1]["type"] == "broll":
                actions.append("convert overlapping post-hook broll → aroll")
                beats[1] = _to_aroll(beats[1])
            beats[1]["start_time"] = need
            beats[1]["start"] = need

    # 3) Enforce min gap: convert later broll → aroll when too close
    changed = True
    while changed:
        changed = False
        idxs = _broll_indices(beats)
        for a, b in zip(idxs, idxs[1:]):
            gap = beats[b]["start_time"] - beats[a]["end_time"]
            if gap < rules.min_gap_between_brolls:
                actions.append(
                    f"drop broll @ {beats[b]['start_time']:.1f}s (gap {gap:.1f}s too small)"
                )
                beats[b] = _to_aroll(beats[b])
                changed = True
                break

    # 4) Max B-roll count: drop from the end
    idxs = _broll_indices(beats)
    while len(idxs) > rules.max_broll_count:
        drop_i = idxs[-1]
        actions.append(f"drop excess broll @ {beats[drop_i]['start_time']:.1f}s (max {rules.max_broll_count})")
        beats[drop_i] = _to_aroll(beats[drop_i])
        idxs = _broll_indices(beats)

    # 5) A-roll share: drop brolls from the end until share OK
    def aroll_share() -> float:
        a = sum((b["end_time"] - b["start_time"]) for b in beats if b["type"] == "aroll")
        return a / max(total_duration, 0.01)

    idxs = _broll_indices(beats)
    while idxs and aroll_share() < rules.min_aroll_share:
        drop_i = idxs[-1]
        actions.append(f"drop broll @ {beats[drop_i]['start_time']:.1f}s (aroll share)")
        beats[drop_i] = _to_aroll(beats[drop_i])
        idxs = _broll_indices(beats)

    # 6) Ending must be aroll
    end_start = max(0.0, total_duration - rules.min_end_aroll_seconds)
    for i, b in enumerate(beats):
        if b["type"] == "broll" and b["end_time"] > end_start:
            # Trim broll so it ends before end_start, or convert if too short after trim
            if b["start_time"] >= end_start:
                actions.append(f"convert ending broll @ {b['start_time']:.1f}s → aroll")
                beats[i] = _to_aroll(b)
            else:
                nb = dict(b)
                nb["end_time"] = end_start
                nb["end_seconds"] = end_start
                if nb["end_time"] - nb["start_time"] < rules.min_broll_seconds:
                    actions.append(f"convert near-end short broll @ {b['start_time']:.1f}s → aroll")
                    beats[i] = _to_aroll(b)
                else:
                    actions.append(f"trim broll before end aroll @ {end_start:.1f}s")
                    beats[i] = nb

    # Ensure a trailing aroll covers the end
    if not beats or beats[-1]["end_time"] < total_duration - 0.05 or beats[-1]["type"] != "aroll":
        # Extend last aroll or append
        last_aroll_i = next((i for i in range(len(beats) - 1, -1, -1) if beats[i]["type"] == "aroll"), None)
        if last_aroll_i is not None and beats[last_aroll_i]["start_time"] <= end_start:
            beats[last_aroll_i] = dict(beats[last_aroll_i])
            beats[last_aroll_i]["end_time"] = total_duration
            beats[last_aroll_i]["end_seconds"] = total_duration
            actions.append("extend final aroll to video end")
        else:
            beats.append(
                {
                    "type": "aroll",
                    "start": end_start,
                    "start_time": end_start,
                    "end_seconds": total_duration,
                    "end_time": total_duration,
                    "suggestion": "",
                }
            )
            actions.append("append final aroll")

    # Merge adjacent arolls for cleanliness
    beats = _merge_adjacent_arolls(beats)

    plan = dict(plan)
    plan["beats"] = beats
    plan["total_duration_seconds"] = total_duration
    plan["editor_rules"] = asdict(rules)
    plan["editor_repairs"] = actions
    return plan, actions


def _merge_adjacent_arolls(beats: list[dict]) -> list[dict]:
    if not beats:
        return beats
    out = [dict(beats[0])]
    for b in beats[1:]:
        prev = out[-1]
        if prev["type"] == "aroll" and b["type"] == "aroll":
            prev["end_time"] = max(prev["end_time"], b["end_time"])
            prev["end_seconds"] = prev["end_time"]
            continue
        out.append(dict(b))
    return out


def apply_editor_guardrails(
    plan: dict,
    total_duration: float,
    rules: EditorRules | None = None,
) -> dict:
    """Validate; if issues, repair; re-validate and attach metadata."""
    rules = rules or load_editor_rules()
    issues = validate_editor_plan(plan, total_duration, rules)
    repairs: list[str] = []
    if issues:
        plan, repairs = repair_editor_plan(plan, total_duration, rules)
        issues_after = validate_editor_plan(plan, total_duration, rules)
    else:
        issues_after = []
        plan = dict(plan)
        plan["beats"] = normalize_beats(plan, total_duration)
        plan["editor_rules"] = asdict(rules)
        plan["editor_repairs"] = []

    plan["editor_validation"] = {
        "issues_before": issues,
        "issues_after": issues_after,
        "ok": len(issues_after) == 0,
    }
    if repairs:
        print(f"[Editor] Repaired {len(repairs)} issue(s):")
        for a in repairs:
            print(f"  - {a}")
    elif issues:
        print(f"[Editor] Validation issues (unrepaired leftovers): {issues_after}")
    else:
        n_broll = sum(1 for b in plan["beats"] if b.get("type") == "broll")
        print(f"[Editor] Plan OK ({n_broll} B-rolls, guardrails passed)")
    return plan
