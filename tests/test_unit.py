"""Unit tests for lip-sync guard, keywords, aroll-first, timeline cap, audio filters."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_normalize_and_overlap():
    from script_engine.lip_sync import assert_lip_sync_safe, normalize_words, word_overlap_ratio

    assert normalize_words("Hello, world!") == ["hello", "world"]
    src = "I want to get more done in less time"
    good = "I want to get more done in less time."
    bad = "Productivity hacks that change everything forever"
    assert word_overlap_ratio(src, good) >= 0.85
    assert word_overlap_ratio(src, bad) < 0.5
    assert_lip_sync_safe(src, good)
    with pytest.raises(ValueError):
        assert_lip_sync_safe(src, bad)


def test_extract_keywords():
    from video_engine.pexels_client import extract_keywords

    suggestion = (
        "A child's small hands reaching toward the camera. "
        "Camera: gentle dolly-in. Warm golden light."
    )
    kw = extract_keywords(suggestion)
    assert "Camera" not in kw
    assert "hands" in kw.lower() or "child" in kw.lower()
    assert len(kw.split()) <= 4


def test_enforce_aroll_start():
    from script_engine.editing_plan import _enforce_aroll_start

    plan = {
        "beats": [
            {"type": "broll", "start": 0, "end_seconds": 5, "suggestion": "x"},
            {"type": "aroll", "start": 5, "end_seconds": 10, "suggestion": ""},
        ]
    }
    _enforce_aroll_start(plan)
    assert plan["beats"][0]["type"] == "aroll"


def test_cap_broll_duration():
    from video_engine.timeline import _cap_broll_duration

    timeline = [
        {"start": 0, "end": 3, "type": "aroll", "suggestion": "", "broll_index": None},
        {"start": 3, "end": 12, "type": "broll", "suggestion": "x", "broll_index": 1},
    ]
    capped = _cap_broll_duration(timeline, 5.0)
    broll = [s for s in capped if s["type"] == "broll"][0]
    assert broll["end"] - broll["start"] == pytest.approx(5.0)


def test_align_prefers_start_time():
    from video_engine.timeline import align_plan_to_transcript

    transcript = {
        "segments": [
            {"start": 0, "end": 10, "text": "hello world"},
        ]
    }
    plan = {
        "total_duration_seconds": 100,
        "beats": [
            {
                "type": "aroll",
                "start": 0,
                "end_seconds": 50,
                "start_time": 0,
                "end_time": 4,
                "suggestion": "",
            },
            {
                "type": "broll",
                "start": 50,
                "end_seconds": 100,
                "start_time": 4,
                "end_time": 9,
                "suggestion": "hands typing",
            },
        ],
    }
    timeline = align_plan_to_transcript(transcript, plan)
    assert timeline[0]["end"] == pytest.approx(4.0)
    assert timeline[1]["type"] == "broll"
    assert timeline[1]["broll_index"] == 1


def test_social_audio_filters_default():
    from video_engine.enhance import social_audio_filters

    af = social_audio_filters()
    assert "highpass" in af
    assert "afftdn" in af or "arnndn" in af
    assert "loudnorm" in af or "acompressor" in af


def test_stage_order_modes():
    from pipeline import VIDEO_FIRST_ORDER, TELEPROMPTER_ORDER, stage_order_for_mode
    from schemas.models import PipelineMode

    assert stage_order_for_mode(PipelineMode.video_first)[0] == "merge"
    assert "plan" in VIDEO_FIRST_ORDER
    assert "script" in TELEPROMPTER_ORDER
    assert "plan" not in TELEPROMPTER_ORDER
    assert "enhance" in VIDEO_FIRST_ORDER


def test_create_project_and_manifest(tmp_path, monkeypatch):
    import core.project_store as ps

    monkeypatch.setattr(ps, "PROJECTS_DIR", tmp_path)
    from schemas.models import PipelineMode

    m = ps.create_project(name="demo", topic="fitness tips", mode=PipelineMode.video_first)
    assert m.id.startswith("demo-")
    assert (tmp_path / m.id / "raw_clips").is_dir()
    assert (tmp_path / m.id / "project.json").exists()
    assert (tmp_path / m.id / "topic.txt").read_text().strip() == "fitness tips"


def test_veo_prompt_includes_spoken_line_and_skips_avoid():
    from video_engine.veo_client import _build_style_suffix, _build_veo_prompt

    style = {
        "format": "vertical 9:16, must look like real video not a photograph",
        "visual_tone": "warm home interiors",
        "color_palette": "warm tones",
        "broll_casting": "Same household: parent and child 5-8",
        "avoid": "no logos, no text overlays",
        "mood": "intimate",
    }
    suffix = _build_style_suffix(style, kinetic=False)
    assert "no logos" not in suffix.lower()
    assert "Same household" in suffix
    assert "intimate" not in suffix  # mood omitted from Veo suffix

    suggestion = (
        "A child sitting still at a kitchen table, hands flat on thighs. "
        "Camera: tight push-in. Warm evening light."
    )
    spoken = "Sometimes your most obedient child is actually a frightened child."
    full = _build_veo_prompt(
        suggestion,
        4,
        suffix,
        spoken_line=spoken,
    )
    assert "frightened child" in full
    assert "Illustrates the spoken line" in full
    assert "hands flat on thighs" in full
    assert "Single continuous 4 second clip" in full


def test_spoken_text_for_range(tmp_path):
    from video_engine.veo_client import spoken_text_for_range

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "transcript.json").write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 3.0, "text": "Hello there"},
                    {"start": 3.0, "end": 7.0, "text": "frightened child"},
                    {"start": 7.0, "end": 10.0, "text": "afterward"},
                ]
            }
        ),
        encoding="utf-8",
    )
    text = spoken_text_for_range(tmp_path, 3.5, 6.5)
    assert "frightened child" in text
    assert "Hello" not in text
    assert spoken_text_for_range(tmp_path, 20, 25) == ""


def test_owner_scoped_list(tmp_path, monkeypatch):
    import core.project_store as ps

    monkeypatch.setattr(ps, "PROJECTS_DIR", tmp_path)
    from schemas.models import PipelineMode

    a = ps.create_project(name="a", mode=PipelineMode.video_first, owner_id="usr_a")
    b = ps.create_project(name="b", mode=PipelineMode.video_first, owner_id="usr_b")
    orphan = ps.create_project(name="orphan", mode=PipelineMode.video_first, owner_id=None)
    mine = ps.list_projects(owner_id="usr_a")
    ids = {r["id"] for r in mine}
    assert a.id in ids
    assert b.id not in ids
    assert orphan.id not in ids


def test_media_token_roundtrip(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret-at-least-32-chars!!")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    from core.auth import decode_access_token, issue_dev_token, issue_media_token

    _, user = issue_dev_token(email="media@test.local")
    media, ttl = issue_media_token(user)
    assert ttl >= 300
    decoded = decode_access_token(media)
    assert decoded.id == user.id
    assert decoded.email == user.email


def test_production_rejects_dev_auth(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret-at-least-32-chars!!")
    from core.auth import validate_auth_config

    with pytest.raises(RuntimeError, match="AUTH_MODE=dev"):
        validate_auth_config()


def test_usage_daily_limit(tmp_path, monkeypatch):
    import core.usage_limits as ul
    from fastapi import HTTPException

    monkeypatch.setattr(ul, "_USAGE_DIR", tmp_path / ".usage")
    monkeypatch.setenv("RATE_LIMIT_JOBS_PER_DAY", "2")
    ul.check_and_increment("usr_x", "job")
    ul.check_and_increment("usr_x", "job")
    with pytest.raises(HTTPException) as ei:
        ul.check_and_increment("usr_x", "job")
    assert ei.value.status_code == 429
