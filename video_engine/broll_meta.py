"""Per-clip B-roll metadata: prompts, chat, version history."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal, Optional

from schemas.models import BrollChatTurn, BrollClipMeta, BrollVersion, utc_now


def meta_path(project_dir: Path, index: int) -> Path:
    return project_dir / "broll" / f"{int(index):03d}.meta.json"


def clip_path(project_dir: Path, index: int) -> Path:
    return project_dir / "broll" / f"{int(index):03d}.mp4"


def load_meta(project_dir: Path, index: int) -> Optional[BrollClipMeta]:
    path = meta_path(project_dir, index)
    if not path.exists():
        return None
    return BrollClipMeta.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_meta(project_dir: Path, meta: BrollClipMeta) -> Path:
    path = meta_path(project_dir, int(meta.broll_index))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        meta.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def list_broll_metas(project_dir: Path) -> list[BrollClipMeta]:
    broll_dir = project_dir / "broll"
    if not broll_dir.exists():
        return []
    out: list[BrollClipMeta] = []
    for path in sorted(broll_dir.glob("*.meta.json")):
        try:
            out.append(BrollClipMeta.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    # Also surface clips that have video but no meta yet
    for path in sorted(broll_dir.glob("*.mp4")):
        stem = path.stem
        if not stem.isdigit() or stem == "filler":
            continue
        idx = int(stem)
        if any(int(m.broll_index) == idx for m in out):
            continue
        out.append(
            BrollClipMeta(
                broll_index=idx,
                source="reuse",
                path=path.name,
                suggestion="",
            )
        )
    out.sort(key=lambda m: int(m.broll_index) if str(m.broll_index).isdigit() else 999)
    return out


def write_generation_meta(
    project_dir: Path,
    *,
    index: int,
    source: Literal["pexels", "veo", "reuse", "skipped_keep_aroll"],
    suggestion: str,
    spoken_text: str = "",
    full_prompt: str = "",
    model: str = "",
    latency_ms: float | None = None,
    estimated_cost_usd: float | None = None,
    trace_id: str | None = None,
    detail: str | None = None,
    used_image: bool = False,
) -> BrollClipMeta:
    """Create or replace meta after an initial pipeline generation (clears chat)."""
    existing = load_meta(project_dir, index)
    chat = existing.chat if existing else []
    versions = list(existing.versions) if existing else []

    path_name = f"{int(index):03d}.mp4" if source not in ("skipped_keep_aroll",) else None
    version_n = len(versions) + 1
    if source in ("veo", "pexels") and path_name:
        versions.append(
            BrollVersion(
                version=version_n,
                prompt=full_prompt or suggestion,
                path=path_name,
                message=None,
                model=model or None,
                latency_ms=latency_ms,
                estimated_cost_usd=estimated_cost_usd,
                used_image=used_image,
                created_at=utc_now(),
            )
        )

    meta = BrollClipMeta(
        broll_index=index,
        source=source,
        suggestion=suggestion,
        spoken_text=spoken_text,
        full_prompt=full_prompt or suggestion,
        path=path_name,
        model=model or None,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost_usd,
        trace_id=trace_id,
        detail=detail,
        chat=chat if source == "reuse" else [],
        versions=versions if source != "reuse" else (existing.versions if existing else []),
        updated_at=utc_now(),
        created_at=existing.created_at if existing else utc_now(),
    )
    if source == "reuse" and existing:
        # Keep existing meta; just bump updated_at
        existing.updated_at = utc_now()
        save_meta(project_dir, existing)
        return existing

    save_meta(project_dir, meta)
    return meta


def archive_current_clip(project_dir: Path, index: int) -> str | None:
    """Move current NNN.mp4 → NNN.vK.mp4. Returns archived filename or None."""
    src = clip_path(project_dir, index)
    if not src.exists():
        return None
    meta = load_meta(project_dir, index)
    n = len(meta.versions) if meta else 0
    # Next archive slot
    k = max(n, 1)
    while True:
        dest = project_dir / "broll" / f"{int(index):03d}.v{k}.mp4"
        if not dest.exists():
            break
        k += 1
    shutil.move(str(src), str(dest))
    return dest.name


def append_edit_result(
    project_dir: Path,
    *,
    index: int,
    user_message: str,
    assistant_note: str,
    full_prompt: str,
    model: str,
    latency_ms: float | None,
    estimated_cost_usd: float | None,
    used_image: bool,
    keyframe_path: str | None = None,
    trace_id: str | None = None,
) -> BrollClipMeta:
    meta = load_meta(project_dir, index)
    if meta is None:
        meta = BrollClipMeta(broll_index=index, source="veo", suggestion="")

    path_name = f"{int(index):03d}.mp4"
    version_n = len(meta.versions) + 1
    meta.chat.append(
        BrollChatTurn(
            role="user",
            content=user_message,
            created_at=utc_now(),
        )
    )
    meta.chat.append(
        BrollChatTurn(
            role="assistant",
            content=assistant_note,
            prompt=full_prompt,
            created_at=utc_now(),
        )
    )
    meta.versions.append(
        BrollVersion(
            version=version_n,
            prompt=full_prompt,
            path=path_name,
            message=user_message,
            model=model,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost_usd,
            used_image=used_image,
            keyframe=keyframe_path,
            created_at=utc_now(),
        )
    )
    meta.full_prompt = full_prompt
    meta.path = path_name
    meta.source = "veo"
    meta.model = model
    meta.latency_ms = latency_ms
    meta.estimated_cost_usd = estimated_cost_usd
    meta.trace_id = trace_id
    meta.updated_at = utc_now()
    save_meta(project_dir, meta)
    return meta


def extract_keyframe(video_path: Path, out_path: Path, *, at_seconds: float = 0.5) -> Path:
    """Extract a single JPEG frame via ffmpeg."""
    import subprocess

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, at_seconds)),
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    if not out_path.exists():
        raise RuntimeError(f"Keyframe extraction failed: {out_path}")
    return out_path
