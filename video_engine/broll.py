"""
B-roll: Veo-only generation (Pexels helpers retained but unused).
Per-clip skip keeps A-roll when no match — no silent placeholder success.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.config import env_int, load_env, truthy
from schemas.models import BrollSourceEntry

CATEGORY_GENERATE = "broll_generate"


def load_timeline(project_dir: Path) -> tuple[list[dict], list[dict]]:
    path = project_dir / "cuts" / "timeline.json"
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("timeline", []), data.get("filler_ranges", [])


def get_broll_for_fillers(project_dir: Path) -> list[tuple[float, float, Path | None]]:
    """Deprecated filler-clip helper."""
    cuts_path = project_dir / "cuts" / "filler_segments.json"
    if not cuts_path.exists():
        return []
    segments = json.loads(cuts_path.read_text(encoding="utf-8"))
    filler_clip = project_dir / "broll" / "filler.mp4"
    return [
        (s["start"], s["end"], filler_clip if filler_clip.exists() else None)
        for s in segments
    ]


def _resolve_use_veo() -> bool:
    """Veo enabled when BROLL_USE_VEO=1 or BROLL_VEO_ONLY=1 (default on)."""
    load_env()
    if _resolve_veo_only():
        return True
    return truthy("BROLL_USE_VEO", "1")


def _resolve_veo_only() -> bool:
    """Product default: Veo only (skip Pexels)."""
    load_env()
    return truthy("BROLL_VEO_ONLY", "1")


def _veo_max_clips() -> int:
    return env_int("VEO_MAX_CLIPS", 5)


def _veo_parallelism() -> int:
    return max(1, env_int("VEO_PARALLELISM", 3))


def _veo_clip_seconds() -> int:
    """Lite/Fast support 4, 6, or 8 — default 4 for lowest cost."""
    raw = env_int("VEO_DURATION_SECONDS", 4)
    allowed = (4, 6, 8)
    if raw in allowed:
        return raw
    return min(allowed, key=lambda x: abs(x - raw))


def _try_pexels(suggestion: str, output_path: Path) -> bool:
    """Unused in product path (Veo-only). Kept for optional future use."""
    try:
        from .pexels_client import download_video, get_pexels_key

        if not get_pexels_key():
            return False
        result = download_video(suggestion, output_path)
        return result is not None
    except Exception as e:
        print(f"  [Pexels] Failed: {e}")
        return False


def _try_pexels_filler(output_path: Path) -> bool:
    """Unused in product path (Veo-only). Kept for optional future use."""
    try:
        from .pexels_client import download_filler, get_pexels_key

        if not get_pexels_key():
            return False
        result = download_filler(output_path)
        return result is not None
    except Exception as e:
        print(f"  [Pexels] Filler failed: {e}")
        return False


def _try_veo(
    suggestion: str,
    output_path: Path,
    project_dir: Path,
    *,
    start: float | None = None,
    end: float | None = None,
):
    from .veo_client import generate_video, spoken_text_for_range

    spoken = ""
    if start is not None and end is not None:
        spoken = spoken_text_for_range(project_dir, float(start), float(end))

    result = generate_video(
        suggestion,
        output_path,
        duration_seconds=_veo_clip_seconds(),
        aspect_ratio="9:16",
        project_dir=project_dir,
        spoken_line=spoken or None,
        return_result=True,
    )
    print(f"  [Veo] Generated: {output_path.name}")
    return result


def _try_veo_filler(output_path: Path, project_dir: Path) -> bool:
    from .veo_client import generate_filler_clip

    generate_filler_clip(output_path, aspect_ratio="9:16", project_dir=project_dir)
    print(f"  [Veo] Generated filler: {output_path.name}")
    return True


def _spoken_for_range(project_dir: Path, start: float, end: float) -> str:
    try:
        from .veo_client import spoken_text_for_range

        return spoken_text_for_range(project_dir, start, end)
    except Exception:
        return ""


def generate_broll_from_timeline(
    project_dir: Path,
    use_veo: bool | None = None,
) -> list[BrollSourceEntry]:
    """
    Source each broll clip via Veo (parallel). Missing matches → skipped_keep_aroll.

    Returns list of BrollSourceEntry for run_report.
    """
    from core.tracing import flush, get_trace_context, observe, set_trace_context
    from video_engine import broll_meta

    if use_veo is None:
        use_veo = _resolve_use_veo()
    veo_budget = _veo_max_clips()
    workers = _veo_parallelism()
    veo_used = 0
    veo_lock = threading.Lock()

    # Product path: Veo-only (Pexels skipped).
    print(f"  [B-roll] Veo-only mode (parallelism={workers}, max_clips={veo_budget}).")
    if not use_veo:
        print("  [B-roll] Veo disabled (BROLL_USE_VEO=0). Clips will skip to A-roll.")

    timeline, _ = load_timeline(project_dir)
    report: list[BrollSourceEntry] = []
    if not timeline:
        return report

    broll_dir = project_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    parent_ctx = get_trace_context()

    pending: list[dict] = []
    need_filler = False

    with observe(
        "broll.stage",
        as_type="span",
        metadata={
            "clips_planned": True,
            "category": CATEGORY_GENERATE,
            "veo_parallelism": workers,
        },
    ):
        for seg in timeline:
            seg_type = seg.get("type")
            if seg_type == "broll":
                idx = seg.get("broll_index")
                if idx is None:
                    continue
                path = broll_dir / f"{int(idx):03d}.mp4"
                suggestion = (seg.get("suggestion") or "abstract professional clip").strip()
                if not suggestion:
                    suggestion = "abstract professional B-roll"
                start = float(seg.get("start", 0) or 0)
                end = float(seg.get("end", 0) or 0)
                spoken = _spoken_for_range(project_dir, start, end)

                if path.exists():
                    report.append(
                        BrollSourceEntry(
                            broll_index=idx,
                            source="reuse",
                            suggestion=suggestion,
                            path=str(path.name),
                        )
                    )
                    broll_meta.write_generation_meta(
                        project_dir,
                        index=int(idx),
                        source="reuse",
                        suggestion=suggestion,
                        spoken_text=spoken,
                    )
                    continue

                pending.append(
                    {
                        "idx": int(idx),
                        "path": path,
                        "suggestion": suggestion,
                        "start": start,
                        "end": end,
                        "spoken": spoken,
                    }
                )
            elif seg_type == "filler":
                path = broll_dir / "filler.mp4"
                if path.exists():
                    report.append(
                        BrollSourceEntry(
                            broll_index="filler",
                            source="reuse",
                            path="filler.mp4",
                        )
                    )
                else:
                    need_filler = True

        def _source_one(item: dict) -> BrollSourceEntry:
            nonlocal veo_used
            set_trace_context(
                user_id=parent_ctx.get("user_id"),
                user_email=parent_ctx.get("user_email"),
                project_id=parent_ctx.get("project_id"),
                job_id=parent_ctx.get("job_id"),
            )
            idx = item["idx"]
            path: Path = item["path"]
            suggestion = item["suggestion"]
            spoken = item["spoken"]

            with observe(
                "broll.clip",
                as_type="span",
                metadata={
                    "broll_index": idx,
                    "suggestion": suggestion[:200],
                    "category": CATEGORY_GENERATE,
                },
            ) as clip_span:
                if not use_veo:
                    skip_detail = "Veo disabled (set BROLL_USE_VEO=1 or BROLL_VEO_ONLY=1)"
                    clip_span.update(level="WARNING", status_message=skip_detail)
                    print(f"  [SKIP] Keep A-roll: {path.name} — {skip_detail}")
                    broll_meta.write_generation_meta(
                        project_dir,
                        index=idx,
                        source="skipped_keep_aroll",
                        suggestion=suggestion,
                        spoken_text=spoken,
                        detail=skip_detail,
                    )
                    return BrollSourceEntry(
                        broll_index=idx,
                        source="skipped_keep_aroll",
                        suggestion=suggestion,
                        detail=skip_detail,
                    )

                with veo_lock:
                    if veo_used >= veo_budget:
                        skip_detail = f"Veo budget exhausted (VEO_MAX_CLIPS={veo_budget})"
                        clip_span.update(level="WARNING", status_message=skip_detail)
                        print(f"  [SKIP] Keep A-roll: {path.name} — {skip_detail}")
                        broll_meta.write_generation_meta(
                            project_dir,
                            index=idx,
                            source="skipped_keep_aroll",
                            suggestion=suggestion,
                            spoken_text=spoken,
                            detail=skip_detail,
                        )
                        return BrollSourceEntry(
                            broll_index=idx,
                            source="skipped_keep_aroll",
                            suggestion=suggestion,
                            detail=skip_detail,
                        )
                    veo_used += 1

                try:
                    veo_result = _try_veo(
                        suggestion,
                        path,
                        project_dir,
                        start=item["start"],
                        end=item["end"],
                    )
                    broll_meta.write_generation_meta(
                        project_dir,
                        index=idx,
                        source="veo",
                        suggestion=suggestion,
                        spoken_text=spoken,
                        full_prompt=veo_result.full_prompt,
                        model=veo_result.model,
                        latency_ms=veo_result.latency_ms,
                        estimated_cost_usd=veo_result.estimated_cost_usd,
                        used_image=veo_result.used_image,
                    )
                    clip_span.update(
                        output={"path": path.name, "source": "veo"},
                        cost_usd=veo_result.estimated_cost_usd,
                        metadata={
                            "category": CATEGORY_GENERATE,
                            "latency_ms": veo_result.latency_ms,
                        },
                    )
                    return BrollSourceEntry(
                        broll_index=idx,
                        source="veo",
                        suggestion=suggestion,
                        path=str(path.name),
                    )
                except Exception as e:
                    skip_detail = f"Veo failed: {e}"
                    print(f"  [Veo] Failed for {path.name}: {e}")
                    clip_span.update(level="ERROR", status_message=skip_detail)
                    broll_meta.write_generation_meta(
                        project_dir,
                        index=idx,
                        source="skipped_keep_aroll",
                        suggestion=suggestion,
                        spoken_text=spoken,
                        detail=skip_detail,
                    )
                    return BrollSourceEntry(
                        broll_index=idx,
                        source="skipped_keep_aroll",
                        suggestion=suggestion,
                        detail=skip_detail,
                    )

        if pending:
            with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
                futures = [pool.submit(_source_one, item) for item in pending]
                for fut in as_completed(futures):
                    report.append(fut.result())

        if need_filler:
            path = broll_dir / "filler.mp4"
            if use_veo:
                with veo_lock:
                    can = veo_used < veo_budget
                    if can:
                        veo_used += 1
                if can:
                    try:
                        set_trace_context(
                            user_id=parent_ctx.get("user_id"),
                            user_email=parent_ctx.get("user_email"),
                            project_id=parent_ctx.get("project_id"),
                            job_id=parent_ctx.get("job_id"),
                        )
                        with observe(
                            "broll.filler",
                            as_type="span",
                            metadata={"category": CATEGORY_GENERATE},
                        ):
                            _try_veo_filler(path, project_dir)
                        report.append(
                            BrollSourceEntry(
                                broll_index="filler",
                                source="veo",
                                path="filler.mp4",
                            )
                        )
                    except Exception as e:
                        print(f"  [Veo] Filler failed: {e}")
                        report.append(
                            BrollSourceEntry(
                                broll_index="filler",
                                source="skipped_keep_aroll",
                                detail=f"Filler clip unavailable: {e}",
                            )
                        )
                else:
                    report.append(
                        BrollSourceEntry(
                            broll_index="filler",
                            source="skipped_keep_aroll",
                            detail=f"Veo budget exhausted (VEO_MAX_CLIPS={veo_budget})",
                        )
                    )
            else:
                report.append(
                    BrollSourceEntry(
                        broll_index="filler",
                        source="skipped_keep_aroll",
                        detail="Filler clip unavailable",
                    )
                )

        flush()

    # Stable order: numeric indices then filler
    def _sort_key(e: BrollSourceEntry):
        if e.broll_index == "filler":
            return (1, 0)
        try:
            return (0, int(e.broll_index))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return (0, 0)

    report.sort(key=_sort_key)

    n_veo = sum(1 for r in report if r.source == "veo")
    n_reuse = sum(1 for r in report if r.source == "reuse")
    n_skip = sum(1 for r in report if r.source == "skipped_keep_aroll")
    print(f"  B-roll summary: {n_veo} Veo, {n_reuse} reuse, {n_skip} skip→A-roll")

    report_path = broll_dir / "source_report.json"
    report_path.write_text(
        json.dumps([e.model_dump() for e in report], indent=2),
        encoding="utf-8",
    )
    return report
