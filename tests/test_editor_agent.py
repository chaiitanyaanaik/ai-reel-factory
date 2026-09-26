"""Tests for editor agent validate + repair guardrails."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from script_engine.editor_agent import (
    EditorRules,
    apply_editor_guardrails,
    repair_editor_plan,
    validate_editor_plan,
)


def _plan(beats, total=60.0):
    return {"beats": beats, "total_duration_seconds": total}


def test_validate_catches_too_many_brolls():
    rules = EditorRules(max_broll_count=2)
    beats = [{"type": "aroll", "start_time": 0, "end_time": 5}]
    t = 5.0
    for i in range(4):
        t += 1
        beats.append({"type": "broll", "start_time": t, "end_time": t + 3, "suggestion": "x"})
        t += 3
        beats.append({"type": "aroll", "start_time": t, "end_time": t + 4})
        t += 4
    issues = validate_editor_plan(_plan(beats, t), t, rules)
    assert any("too many brolls" in i for i in issues)


def test_repair_enforces_max_count_and_gaps():
    rules = EditorRules(
        max_broll_count=5,
        min_gap_between_brolls=3.5,
        min_broll_seconds=2.0,
        max_broll_seconds=5.0,
        min_hook_aroll_seconds=3.0,
        min_end_aroll_seconds=2.5,
        min_aroll_share=0.55,
    )
    # Dense brolls that violate gap + count
    beats = [
        {"type": "broll", "start_time": 0, "end_time": 4, "suggestion": "bad hook broll"},
        {"type": "broll", "start_time": 4.2, "end_time": 8, "suggestion": "too close"},
        {"type": "broll", "start_time": 8.5, "end_time": 12, "suggestion": "x"},
        {"type": "broll", "start_time": 12.5, "end_time": 16, "suggestion": "x"},
        {"type": "broll", "start_time": 16.5, "end_time": 20, "suggestion": "x"},
        {"type": "broll", "start_time": 20.5, "end_time": 24, "suggestion": "x"},
        {"type": "broll", "start_time": 50, "end_time": 58, "suggestion": "too long end"},
    ]
    total = 60.0
    repaired, actions = repair_editor_plan(_plan(beats, total), total, rules)
    assert actions
    issues = validate_editor_plan(repaired, total, rules)
    assert issues == [], issues
    n_broll = sum(1 for b in repaired["beats"] if b["type"] == "broll")
    assert n_broll <= rules.max_broll_count
    assert repaired["beats"][0]["type"] == "aroll"


def test_apply_guardrails_ok_plan_untouched_counts():
    rules = EditorRules()
    total = 40.0
    beats = [
        {"type": "aroll", "start_time": 0, "end_time": 5},
        {"type": "broll", "start_time": 5, "end_time": 9, "suggestion": "hands typing"},
        {"type": "aroll", "start_time": 9, "end_time": 14},
        {"type": "broll", "start_time": 14, "end_time": 18, "suggestion": "city street"},
        {"type": "aroll", "start_time": 18, "end_time": 40},
    ]
    out = apply_editor_guardrails(_plan(beats, total), total, rules)
    assert out["editor_validation"]["ok"] is True
    assert sum(1 for b in out["beats"] if b["type"] == "broll") == 2
