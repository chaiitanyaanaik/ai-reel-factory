"""
B-roll: Pexels-first with optional Veo fallback (opt-in, capped).
Per-clip skip keeps A-roll when no match — no silent placeholder success.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.config import env_int, load_env, truthy
from schemas.models import BrollSourceEntry


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
    """Veo enabled when BROLL_USE_VEO=1 or BROLL_VEO_ONLY=1."""
    load_env()
    if _resolve_veo_only():
        return True
    return truthy("BROLL_USE_VEO", "0")


def _resolve_veo_only() -> bool:
    load_env()
    return truthy("BROLL_VEO_ONLY", "0")


def _veo_max_clips() -> int:
    return env_int("VEO_MAX_CLIPS", 5)


def _veo_clip_seconds() -> int:
    """Lite/Fast support 4, 6, or 8 — default 4 for lowest cost."""
    raw = env_int("VEO_DURATION_SECONDS", 4)
    allowed = (4, 6, 8)
    if raw in allowed:
        return raw
    # Snap to nearest allowed
    return min(allowed, key=lambda x: abs(x - raw))


def _try_pexels(suggestion: str, output_path: Path) -> bool:
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


def generate_broll_from_timeline(
    project_dir: Path,
    use_veo: bool | None = None,
) -> list[BrollSourceEntry]:
    """
    Source each broll clip. Missing matches → skipped_keep_aroll (render shows A-roll).

    Returns list of BrollSourceEntry for run_report.
    """
    from core.tracing import observe
    from video_engine import broll_meta

    if use_veo is None:
        use_veo = _resolve_use_veo()
    veo_only = _resolve_veo_only()
    veo_budget = _veo_max_clips()
    veo_used = 0

    if veo_only:
        print("  [B-roll] Veo-only mode: skipping Pexels.")
    elif not use_veo:
        print("  [B-roll] Veo disabled (BROLL_USE_VEO=0). Pexels only.")

    timeline, _ = load_timeline(project_dir)
    report: list[BrollSourceEntry] = []
    if not timeline:
        return report

    broll_dir = project_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    with observe("broll.stage", as_type="span", metadata={"clips_planned": True}):
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
                spoken = ""
                try:
                    from .veo_client import spoken_text_for_range

                    spoken = spoken_text_for_range(project_dir, start, end)
                except Exception:
                    pass

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

                with observe(
                    "broll.clip",
                    as_type="span",
                    metadata={"broll_index": idx, "suggestion": suggestion[:200]},
                ):
                    if not veo_only and _try_pexels(suggestion, path):
                        report.append(
                            BrollSourceEntry(
                                broll_index=idx,
                                source="pexels",
                                suggestion=suggestion,
                                path=str(path.name),
                            )
                        )
                        broll_meta.write_generation_meta(
                            project_dir,
                            index=int(idx),
                            source="pexels",
                            suggestion=suggestion,
                            spoken_text=spoken,
                            full_prompt=suggestion,
                        )
                        continue

                    if use_veo and veo_used < veo_budget:
                        try:
                            veo_result = _try_veo(
                                suggestion,
                                path,
                                project_dir,
                                start=start,
                                end=end,
                            )
                            veo_used += 1
                            report.append(
                                BrollSourceEntry(
                                    broll_index=idx,
                                    source="veo",
                                    suggestion=suggestion,
                                    path=str(path.name),
                                )
                            )
                            broll_meta.write_generation_meta(
                                project_dir,
                                index=int(idx),
                                source="veo",
                                suggestion=suggestion,
                                spoken_text=spoken,
                                full_prompt=veo_result.full_prompt,
                                model=veo_result.model,
                                latency_ms=veo_result.latency_ms,
                                estimated_cost_usd=veo_result.estimated_cost_usd,
                                used_image=veo_result.used_image,
                            )
                            continue
                        except Exception as e:
                            print(f"  [Veo] Failed for {path.name}: {e}")
                            skip_detail = f"Veo failed: {e}"
                    elif not use_veo:
                        skip_detail = "Veo disabled (set BROLL_USE_VEO=1 or BROLL_VEO_ONLY=1)"
                    elif veo_used >= veo_budget:
                        skip_detail = f"Veo budget exhausted (VEO_MAX_CLIPS={veo_budget})"
                    else:
                        skip_detail = "No B-roll source available"

                    print(f"  [SKIP] Keep A-roll (no B-roll clip): {path.name} — {skip_detail}")
                    report.append(
                        BrollSourceEntry(
                            broll_index=idx,
                            source="skipped_keep_aroll",
                            suggestion=suggestion,
                            detail=skip_detail,
                        )
                    )
                    broll_meta.write_generation_meta(
                        project_dir,
                        index=int(idx),
                        source="skipped_keep_aroll",
                        suggestion=suggestion,
                        spoken_text=spoken,
                        detail=skip_detail,
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
                    continue
                if not veo_only and _try_pexels_filler(path):
                    report.append(
                        BrollSourceEntry(
                            broll_index="filler",
                            source="pexels",
                            path="filler.mp4",
                        )
                    )
                    continue
                if use_veo and veo_used < veo_budget:
                    try:
                        _try_veo_filler(path, project_dir)
                        veo_used += 1
                        report.append(
                            BrollSourceEntry(
                                broll_index="filler",
                                source="veo",
                                path="filler.mp4",
                            )
                        )
                        continue
                    except Exception as e:
                        print(f"  [Veo] Filler failed: {e}")
                report.append(
                    BrollSourceEntry(
                        broll_index="filler",
                        source="skipped_keep_aroll",
                        detail="Filler clip unavailable",
                    )
                )

    n_pexels = sum(1 for r in report if r.source == "pexels")
    n_veo = sum(1 for r in report if r.source == "veo")
    n_reuse = sum(1 for r in report if r.source == "reuse")
    n_skip = sum(1 for r in report if r.source == "skipped_keep_aroll")
    print(
        f"  B-roll summary: {n_pexels} Pexels, {n_veo} Veo, {n_reuse} reuse, {n_skip} skip→A-roll"
    )

    report_path = broll_dir / "source_report.json"
    report_path.write_text(
        json.dumps([e.model_dump() for e in report], indent=2),
        encoding="utf-8",
    )
    return report
