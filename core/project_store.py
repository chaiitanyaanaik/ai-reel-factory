"""Filesystem project store: dirs, project.json, run_report.json."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from schemas.models import (
    JobStatus,
    PipelineMode,
    ProjectManifest,
    RunReport,
    utc_now,
)

from .config import ROOT

PROJECTS_DIR = ROOT / "projects"

PROJECT_SUBDIRS = (
    "raw_clips",
    "merged",
    "transcripts",
    "cuts",
    "broll",
    "subtitles",
    "cover",
    "final",
)


def get_project_dir(project_id: str) -> Path:
    p = PROJECTS_DIR / project_id
    if not p.is_dir():
        raise FileNotFoundError(f"Project not found: {p}")
    return p


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return s or f"project-{uuid.uuid4().hex[:8]}"


def create_project(
    name: Optional[str] = None,
    topic: Optional[str] = None,
    mode: PipelineMode = PipelineMode.video_first,
    owner_id: Optional[str] = None,
) -> ProjectManifest:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    # Prefer UUID ids for multi-user; keep readable slug prefix when name given.
    if name:
        project_id = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
    else:
        project_id = f"project-{uuid.uuid4().hex[:10]}"
    project_dir = PROJECTS_DIR / project_id
    # Extremely unlikely collision with UUID suffix; still guard.
    if project_dir.exists():
        project_id = f"project-{uuid.uuid4().hex[:12]}"
        project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True)
    for sub in PROJECT_SUBDIRS:
        (project_dir / sub).mkdir(exist_ok=True)
    if topic:
        (project_dir / "topic.txt").write_text(topic.strip() + "\n", encoding="utf-8")
    manifest = ProjectManifest(id=project_id, mode=mode, topic=topic, owner_id=owner_id)
    save_manifest(project_dir, manifest)
    return manifest


def manifest_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def run_report_path(project_dir: Path) -> Path:
    return project_dir / "run_report.json"


def save_manifest(project_dir: Path, manifest: ProjectManifest) -> Path:
    manifest.updated_at = utc_now()
    path = manifest_path(project_dir)
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_manifest(project_dir: Path) -> ProjectManifest:
    path = manifest_path(project_dir)
    if not path.exists():
        # Back-compat for legacy folders without project.json
        return ProjectManifest(
            id=project_dir.name,
            mode=PipelineMode.video_first,
            status=JobStatus.pending,
        )
    return ProjectManifest.model_validate_json(path.read_text(encoding="utf-8"))


def update_manifest(project_dir: Path, **kwargs: Any) -> ProjectManifest:
    manifest = load_manifest(project_dir)
    data = manifest.model_dump()
    data.update(kwargs)
    updated = ProjectManifest.model_validate(data)
    save_manifest(project_dir, updated)
    return updated


def save_run_report(project_dir: Path, report: RunReport) -> Path:
    path = run_report_path(project_dir)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_run_report(project_dir: Path) -> Optional[RunReport]:
    path = run_report_path(project_dir)
    if not path.exists():
        return None
    return RunReport.model_validate_json(path.read_text(encoding="utf-8"))


def list_artifacts(project_dir: Path) -> dict[str, str]:
    """Relative artifact paths that exist."""
    mapping = {
        "reel": "final/reel.mp4",
        "cover": "final/cover.jpg",
        "transcript": "transcripts/transcript.json",
        "timeline": "cuts/timeline.json",
        "script": "raw_script.md",
        "plan": "final_script.json",
        "run_report": "run_report.json",
        "enhanced": "merged/enhanced.mp4",
        "merged": "merged/merged.mp4",
    }
    out: dict[str, str] = {}
    for key, rel in mapping.items():
        if (project_dir / rel).exists():
            out[key] = rel
    return out


def list_projects(owner_id: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Summaries for library UI, newest first.
    When owner_id is set, only that user's projects (plus ownerless if LEGACY_PUBLIC_PROJECTS=1).
    """
    import os

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    legacy = (os.environ.get("LEGACY_PUBLIC_PROJECTS") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    rows: list[dict[str, Any]] = []
    for path in PROJECTS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            manifest = ensure_manifest(path)
        except Exception:
            continue
        if owner_id is not None:
            if manifest.owner_id == owner_id:
                pass
            elif manifest.owner_id is None and legacy:
                pass
            else:
                continue
        artifacts = list_artifacts(path)
        clips = list_clips(path)
        rows.append(
            {
                "id": manifest.id,
                "mode": manifest.mode.value if hasattr(manifest.mode, "value") else manifest.mode,
                "status": manifest.status.value if hasattr(manifest.status, "value") else manifest.status,
                "topic": manifest.topic,
                "owner_id": manifest.owner_id,
                "created_at": manifest.created_at.isoformat() if manifest.created_at else None,
                "updated_at": manifest.updated_at.isoformat() if manifest.updated_at else None,
                "clip_count": len(clips),
                "has_reel": "reel" in artifacts,
                "has_script": "script" in artifacts,
                "has_cover": "cover" in artifacts,
                "artifacts": artifacts,
            }
        )
    rows.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
    return rows


def list_clips(project_dir: Path) -> list[dict[str, Any]]:
    raw = project_dir / "raw_clips"
    if not raw.exists():
        return []
    files = sorted(
        [p for p in raw.iterdir() if p.suffix.lower() in (".mp4", ".mov") and p.is_file()],
        key=lambda p: p.name.lower(),
    )
    out = []
    for i, f in enumerate(files, start=1):
        out.append(
            {
                "index": i,
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "url": f"/projects/{project_dir.name}/clips/{f.name}",
            }
        )
    return out


def reorder_clips(project_dir: Path, order: list[str]) -> list[dict[str, Any]]:
    """
    Rename clips to 01.ext, 02.ext, ... matching the given current filenames order.
    """
    raw = project_dir / "raw_clips"
    raw.mkdir(parents=True, exist_ok=True)
    if not order:
        raise ValueError("order must be a non-empty list of filenames")

    existing = {p.name: p for p in list(raw.glob("*.mp4")) + list(raw.glob("*.mov"))}
    missing = [name for name in order if name not in existing]
    if missing:
        raise FileNotFoundError(f"Unknown clips: {missing}")

    # Two-phase rename to avoid collisions
    staging: list[tuple[Path, Path]] = []
    for i, name in enumerate(order, start=1):
        src = existing[name]
        suffix = src.suffix.lower() or ".mp4"
        staged = raw / f".__stage_{i:02d}{suffix}"
        staging.append((src, staged))

    for src, staged in staging:
        src.rename(staged)

    final_names: list[str] = []
    for i, (_, staged) in enumerate(staging, start=1):
        dest = raw / f"{i:02d}{staged.suffix.lower()}"
        staged.rename(dest)
        final_names.append(dest.name)

    # Remove any clips not in the new order
    keep = set(final_names)
    for p in list(raw.glob("*.mp4")) + list(raw.glob("*.mov")):
        if p.name not in keep and not p.name.startswith("."):
            p.unlink(missing_ok=True)

    return list_clips(project_dir)


def ensure_manifest(project_dir: Path) -> ProjectManifest:
    path = manifest_path(project_dir)
    if not path.exists():
        m = ProjectManifest(id=project_dir.name)
        save_manifest(project_dir, m)
        return m
    return load_manifest(project_dir)
