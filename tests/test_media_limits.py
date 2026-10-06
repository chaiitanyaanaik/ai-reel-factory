"""Duration limit helpers."""
from __future__ import annotations

from core.media_limits import format_duration_label, max_clip_duration_seconds, max_project_duration_seconds


def test_format_duration_label():
    assert format_duration_label(0) == "0:00"
    assert format_duration_label(65) == "1:05"
    assert format_duration_label(180) == "3:00"


def test_default_limits(monkeypatch):
    monkeypatch.delenv("MAX_CLIP_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("MAX_PROJECT_DURATION_SECONDS", raising=False)
    # load_env may refill from .env — set explicitly
    monkeypatch.setenv("MAX_CLIP_DURATION_SECONDS", "180")
    monkeypatch.setenv("MAX_PROJECT_DURATION_SECONDS", "180")
    assert max_clip_duration_seconds() == 180
    assert max_project_duration_seconds() == 180
