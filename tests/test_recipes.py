"""Style recipe resolver + zoom policy (Phase 0/1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.recipes import (
    resolve_recipe,
    select_zoom_ranges,
)
from script_engine.editor_agent import EditorRules, load_editor_rules


def test_default_recipe_is_talking_head():
    cfg = resolve_recipe(None, None)
    assert cfg.name == "talking_head"
    assert cfg.zoom_policy == "alternate_segment"
    assert cfg.max_broll_count is None
    assert cfg.min_gap_between_brolls is None


def test_unknown_recipe_falls_back():
    cfg = resolve_recipe("not-a-real-recipe", "nope")
    assert cfg.name == "talking_head"
    assert cfg.intensity == "balanced"


def test_polish_flag_off_forces_talking_head(monkeypatch):
    monkeypatch.setenv("POLISH_RECIPES", "0")
    cfg = resolve_recipe("tutorial", "punchy")
    assert cfg.name == "talking_head"
    assert cfg.zoom_policy == "alternate_segment"


def test_talking_head_rules_match_env_defaults(monkeypatch):
    monkeypatch.delenv("EDITOR_MAX_BROLL_COUNT", raising=False)
    monkeypatch.delenv("EDITOR_MIN_BROLL_GAP", raising=False)
    monkeypatch.delenv("EDITOR_MIN_AROLL_SHARE", raising=False)
    monkeypatch.delenv("VEO_MAX_CLIPS", raising=False)
    monkeypatch.setenv("POLISH_RECIPES", "1")
    base = load_editor_rules()
    with_recipe = load_editor_rules(resolve_recipe("talking_head"))
    assert with_recipe == base
    assert with_recipe == EditorRules()


def test_tutorial_overrides_gaps_not_count_ceiling(monkeypatch):
    monkeypatch.setenv("POLISH_RECIPES", "1")
    monkeypatch.setenv("VEO_MAX_CLIPS", "5")
    monkeypatch.delenv("EDITOR_MAX_BROLL_COUNT", raising=False)
    rules = load_editor_rules(resolve_recipe("tutorial"))
    assert rules.min_gap_between_brolls == 2.8
    assert rules.min_aroll_share == 0.50
    assert rules.max_broll_count == 5


def test_story_fewer_brolls(monkeypatch):
    monkeypatch.setenv("POLISH_RECIPES", "1")
    monkeypatch.setenv("VEO_MAX_CLIPS", "5")
    rules = load_editor_rules(resolve_recipe("story"))
    assert rules.max_broll_count == 3
    assert rules.min_aroll_share == 0.65


def test_alternate_zoom_matches_legacy():
    segs = [
        {"start": 0, "end": 2},
        {"start": 2, "end": 4},
        {"start": 4, "end": 6},
        {"start": 6, "end": 8},
    ]
    legacy = {(float(s["start"]), float(s["end"])) for i, s in enumerate(segs) if i % 2 == 1}
    assert select_zoom_ranges(segs, "alternate_segment") == legacy


def test_dense_and_sparse_zoom_differ():
    segs = [{"start": float(i), "end": float(i + 1)} for i in range(6)]
    alt = select_zoom_ranges(segs, "alternate_segment")
    dense = select_zoom_ranges(segs, "dense_alternate")
    sparse = select_zoom_ranges(segs, "sparse_presence")
    assert len(dense) > len(alt)
    assert len(sparse) <= len(alt)
    assert (0.0, 1.0) not in dense  # hook unzoomed


def test_recipe_change_invalidates_plan_keeps_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr("core.project_store.PROJECTS_DIR", tmp_path)
    from core import project_store as ps
    from schemas.models import PipelineMode

    m = ps.create_project(name="r", mode=PipelineMode.video_first)
    project_dir = ps.get_project_dir(m.id)
    (project_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (project_dir / "transcripts" / "transcript.json").write_text(
        json.dumps({"text": "hello", "segments": []}), encoding="utf-8"
    )
    (project_dir / "merged").mkdir(parents=True, exist_ok=True)
    (project_dir / "merged" / "merged.mp4").write_bytes(b"fake")
    (project_dir / "final_script.json").write_text("{}", encoding="utf-8")
    (project_dir / "final").mkdir(parents=True, exist_ok=True)
    (project_dir / "final" / "reel.mp4").write_bytes(b"fake")

    updated = ps.update_project(m.id, recipe="tutorial", update_recipe=True)
    assert updated.recipe == "tutorial"
    assert (project_dir / "transcripts" / "transcript.json").exists()
    assert (project_dir / "merged" / "merged.mp4").exists()
    assert not (project_dir / "final_script.json").exists()
    assert not (project_dir / "final" / "reel.mp4").exists()
