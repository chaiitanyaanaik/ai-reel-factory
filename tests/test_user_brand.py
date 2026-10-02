"""Tests for per-user brand merge and neutral craft prompts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_merge_order_global_then_user_then_project(tmp_path, monkeypatch):
    from core import user_brand
    from schemas.models import BrandProfile, ProjectManifest

    # Isolate brand storage under tmp
    monkeypatch.setattr(user_brand, "_USERS_DIR", tmp_path / ".users")

    global_guide = {
        "visual_tone": "global-tone",
        "mood": "global-mood",
        "format": "vertical 9:16",
    }
    (ROOT / "style_guide.json").read_text(encoding="utf-8")  # ensure exists
    monkeypatch.setattr(
        user_brand,
        "ROOT",
        tmp_path,
    )
    (tmp_path / "style_guide.json").write_text(json.dumps(global_guide), encoding="utf-8")

    user_brand.save_user_brand(
        "user_a",
        BrandProfile(niche="fitness", setting="gym floor", broll_casting="athletic coach"),
    )

    project_dir = tmp_path / "proj1"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        ProjectManifest(id="proj1", owner_id="user_a").model_dump_json(),
        encoding="utf-8",
    )
    (project_dir / "style_guide.json").write_text(
        json.dumps({"mood": "project-mood"}),
        encoding="utf-8",
    )

    guide = user_brand.load_merged_style_guide(project_dir)
    assert guide["visual_tone"]  # from brand setting or visual_tone
    assert "gym floor" in (guide.get("visual_tone") or guide.get("visual_world") or "")
    assert guide["broll_casting"] == "athletic coach"
    assert guide["mood"] == "project-mood"  # project wins
    assert guide["niche"] == "fitness"
    assert guide["format"] == "vertical 9:16"


def test_empty_user_yields_neutral_guide(tmp_path, monkeypatch):
    from core import user_brand

    monkeypatch.setattr(user_brand, "_USERS_DIR", tmp_path / ".users")
    monkeypatch.setattr(user_brand, "ROOT", tmp_path)
    (tmp_path / "style_guide.json").write_text(
        json.dumps(
            {
                "visual_tone": "clean lifestyle B-roll",
                "format": "vertical 9:16",
                "avoid": "stock-photo feel",
            }
        ),
        encoding="utf-8",
    )

    guide = user_brand.load_merged_style_guide(user_id=None)
    text = user_brand.style_guide_text(user_id=None)
    assert "astrology" not in json.dumps(guide).lower()
    assert "astrology" not in text.lower()
    assert "parenting" not in json.dumps(guide).lower()
    assert "No brand profile set" in text or "Brand & scene" in text


def test_editor_prompt_uses_brand_not_astrology_hardcode():
    from script_engine.editor_agent import EditorRules, build_editor_prompt

    prompt = build_editor_prompt(
        plain="hello world",
        segment_summary="0-5: hello world",
        topic="focus",
        refs="",
        style="## Brand & scene\n- Visual world: modern office\n- B-roll casting: young professionals",
        total_duration=30.0,
        rules=EditorRules(),
    )
    assert "astrology" not in prompt.lower()
    assert "posh contemporary Indian home" not in prompt
    assert "modern office" in prompt
    assert "young professionals" in prompt
    assert "Obey Brand & scene" in prompt or "Brand & scene" in prompt


def test_list_projects_skips_dot_users(tmp_path, monkeypatch):
    from core import project_store
    from schemas.models import ProjectManifest

    monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path)
    real = tmp_path / "real_proj"
    real.mkdir()
    (real / "project.json").write_text(
        ProjectManifest(id="real_proj", owner_id="u1").model_dump_json(),
        encoding="utf-8",
    )
    users = tmp_path / ".users"
    users.mkdir()
    (users / "brand.json").write_text("{}", encoding="utf-8")

    rows = project_store.list_projects(owner_id="u1")
    ids = [r["id"] for r in rows]
    assert "real_proj" in ids
    assert ".users" not in ids
