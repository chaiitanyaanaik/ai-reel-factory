"""
AI Reel Factory — main workflow orchestrator.

Default mode: video_first
  merge → enhance → transcribe → plan → align → broll → subtitles → render → cover

With PIPELINE_OVERLAP_ENHANCE=1 (default), enhance runs in the background after its
stage slot while plan/align/broll/subtitles proceed; Whisper uses merged.mp4; enhance
joins before render. Never overlaps enhance with Whisper on the same host.

Teleprompter mode: script first (topic → Gemini), then same video chain after clips exist.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_env
from core.project_store import (
    ensure_manifest,
    get_project_dir,
    list_artifacts,
    save_run_report,
    update_manifest,
)
from schemas.models import (
    JobStatus,
    PipelineMode,
    RunReport,
    StageResult,
    utc_now,
)


VIDEO_FIRST_ORDER = [
    "merge",
    "enhance",
    "transcribe",
    "plan",
    "align",
    "broll",
    "subtitles",
    "render",
    "cover",
]

TELEPROMPTER_ORDER = [
    "script",
    "merge",
    "enhance",
    "transcribe",
    "align",
    "broll",
    "subtitles",
    "render",
    "cover",
]


def run_script_stage(project_dir: Path) -> None:
    """Teleprompter: topic → script + editing plan (invented dialogue — record after)."""
    topic_path = project_dir / "topic.txt"
    if not topic_path.exists():
        raise FileNotFoundError(f"Add topic: {topic_path}")
    topic = topic_path.read_text(encoding="utf-8").strip()

    from script_engine.generate_script import generate_script
    from script_engine.editing_plan import script_to_editing_plan, save_editing_plan

    raw_script = generate_script(topic, project_dir=project_dir)
    (project_dir / "raw_script.md").write_text(raw_script, encoding="utf-8")
    plan = script_to_editing_plan(raw_script, project_dir=project_dir)
    save_editing_plan(project_dir, plan)
    print("[OK] Script + editing plan (teleprompter)")


def run_merge_stage(project_dir: Path) -> Path:
    from video_engine.merge_clips import merge_clips

    path = merge_clips(project_dir)
    print(f"[OK] Merged -> {path}")
    return path


def run_enhance_stage(project_dir: Path) -> Path:
    from video_engine.enhance import enhance_audio

    path = enhance_audio(project_dir)
    print(f"[OK] Enhanced audio -> {path}")
    return path


def run_transcribe_stage(project_dir: Path, *, prefer_merged: bool = False) -> Path:
    from video_engine.enhance import resolve_source_video
    from video_engine.transcribe import save_transcript, transcribe

    # When enhance runs in the background, Whisper must not wait on enhanced.mp4.
    # Timestamps match merged video; clean audio is only required at render.
    if prefer_merged:
        video = project_dir / "merged" / "merged.mp4"
        if not video.exists():
            raise FileNotFoundError(f"Run merge first: {video}")
    else:
        video = resolve_source_video(project_dir)
    data = transcribe(video)
    path = save_transcript(project_dir, data)
    print(f"[OK] Transcript -> {path}")
    return path


def run_plan_stage(project_dir: Path) -> None:
    """Video-first: plan from transcript (lip-sync safe)."""
    from script_engine.plan_from_transcript import run_plan_stage as _plan

    _plan(project_dir)


def run_filler_stage(project_dir: Path) -> Path:
    """Deprecated: um/ah detection for optional mute overlays."""
    from video_engine.filler_detection import (
        get_filler_segments,
        load_transcript,
        save_filler_segments,
    )

    transcript = load_transcript(project_dir)
    segments = get_filler_segments(transcript)
    path = save_filler_segments(project_dir, segments)
    print(f"[OK] Filler segments -> {path} ({len(segments)} segments) [deprecated]")
    return path


def run_align_stage(project_dir: Path) -> Path:
    from video_engine.timeline import build_timeline_with_fillers, save_timeline

    timeline, filler_ranges = build_timeline_with_fillers(project_dir)
    path = save_timeline(project_dir, timeline, filler_ranges)
    print(f"[OK] Timeline -> {path} ({len(timeline)} segments)")
    return path


def run_broll_stage(project_dir: Path) -> None:
    from video_engine.broll import generate_broll_from_timeline

    report = generate_broll_from_timeline(project_dir)
    planned = [r for r in report if str(r.broll_index) != "filler"]
    sourced = sum(1 for r in planned if r.source in ("veo", "pexels", "reuse"))
    if planned and sourced == 0:
        details = " ".join((r.detail or "") for r in planned)
        if "429" in details or "quota" in details.lower() or "RESOURCE_EXHAUSTED" in details:
            raise RuntimeError(
                "B-roll failed: Google Veo quota exhausted (429). "
                "Check billing/rate limits at https://ai.dev/rate-limit, then retry."
            )
        raise RuntimeError(
            "B-roll failed: no clips were sourced. Check Veo settings and retry."
        )
    print(f"[OK] B-roll sourced ({len(report)} entries, {sourced} clips)")


def run_subtitles_stage(project_dir: Path) -> Path:
    from video_engine.subtitles import generate_srt

    path = generate_srt(project_dir, strip_fillers=True)
    print(f"[OK] Subtitles -> {path}")
    return path


def run_render_stage(project_dir: Path) -> Path:
    from video_engine.render import render_final

    path = render_final(project_dir, width=1080, height=1920)
    print(f"[OK] Final reel -> {path}")
    return path


def run_cover_stage(project_dir: Path) -> Path:
    from video_engine.cover import generate_cover_from_reel

    path = generate_cover_from_reel(project_dir)
    print(f"[OK] Cover -> {path}")
    return path


STAGES = {
    "script": run_script_stage,
    "merge": run_merge_stage,
    "enhance": run_enhance_stage,
    "transcribe": run_transcribe_stage,
    "plan": run_plan_stage,
    "filler": run_filler_stage,
    "align": run_align_stage,
    "broll": run_broll_stage,
    "subtitles": run_subtitles_stage,
    "render": run_render_stage,
    "cover": run_cover_stage,
}


def stage_order_for_mode(mode: PipelineMode | str) -> list[str]:
    if isinstance(mode, str):
        mode = PipelineMode(mode)
    if mode == PipelineMode.teleprompter:
        return list(TELEPROMPTER_ORDER)
    return list(VIDEO_FIRST_ORDER)


def _load_broll_report(project_dir: Path) -> list:
    import json

    from schemas.models import BrollSourceEntry

    path = project_dir / "broll" / "source_report.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [BrollSourceEntry.model_validate(x) for x in raw]


def _stage_error_message(project_dir: Path, stage: str, err: BaseException) -> str:
    """Prefer a readable stage error; fall back to cover metadata when SDK masks it."""
    text = str(err).strip() or type(err).__name__
    if "generator didn't stop after throw" not in text.lower():
        return text
    if stage == "cover":
        import json

        meta_path = project_dir / "cover" / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                saved = (meta.get("error_message") or "").strip()
                if saved:
                    return saved
            except Exception:
                pass
    cause = err.__cause__ or err.__context__
    if cause is not None and cause is not err:
        nested = str(cause).strip()
        if nested and "generator didn't stop after throw" not in nested.lower():
            return nested
    return text


def run_pipeline(
    project_name: str,
    from_stage: str | None = None,
    to_stage: str | None = None,
    mode: PipelineMode | str | None = None,
    job_id: str | None = None,
    *,
    user_id: str | None = None,
    user_email: str | None = None,
) -> RunReport:
    load_env()
    from core.tracing import clear_trace_context, flush, observe, set_trace_context

    project_dir = get_project_dir(project_name)
    ensure_manifest(project_dir)

    if mode is None:
        manifest = ensure_manifest(project_dir)
        mode = manifest.mode
    if isinstance(mode, str):
        mode = PipelineMode(mode)

    order = stage_order_for_mode(mode)
    if from_stage:
        try:
            start = order.index(from_stage)
        except ValueError:
            raise ValueError(f"Unknown stage for mode {mode.value}: {from_stage}. Use: {order}")
    else:
        start = 0
    if to_stage:
        try:
            end = order.index(to_stage) + 1
        except ValueError:
            raise ValueError(f"Unknown stage for mode {mode.value}: {to_stage}. Use: {order}")
    else:
        end = len(order)

    job_id = job_id or uuid.uuid4().hex[:12]
    manifest = ensure_manifest(project_dir)
    set_trace_context(
        user_id=user_id or manifest.owner_id,
        user_email=user_email,
        project_id=project_name,
        job_id=job_id,
    )

    from core.recipes import resolve_recipe_for_project

    recipe_cfg = resolve_recipe_for_project(project_dir)
    report = RunReport(
        project_id=project_name,
        mode=mode,
        job_id=job_id,
        started_at=utc_now(),
        status=JobStatus.running,
        models={
            "gemini": os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            "veo": os.environ.get("VEO_MODEL", "veo-3.1-lite-generate-preview"),
            "veo_resolution": os.environ.get("VEO_RESOLUTION", "720p"),
            "veo_duration": os.environ.get("VEO_DURATION_SECONDS", "4"),
            "recipe": recipe_cfg.name,
            "intensity": recipe_cfg.intensity,
            "zoom_policy": recipe_cfg.zoom_policy,
        },
        veo_max_clips=int(os.environ.get("VEO_MAX_CLIPS", "5") or 5),
    )
    update_manifest(
        project_dir,
        status=JobStatus.running,
        mode=mode,
        last_job_id=job_id,
        current_stage=order[start] if start < end else None,
        error=None,
    )
    save_run_report(project_dir, report)

    # Phase B: run enhance in the background while plan/align/broll (network) run.
    # Never overlap enhance with Whisper on 8GB hosts — both are RAM-heavy.
    stages_slice = order[start:end]
    overlap_enhance = os.environ.get("PIPELINE_OVERLAP_ENHANCE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    can_overlap = (
        overlap_enhance
        and "enhance" in stages_slice
        and any(s in stages_slice for s in ("plan", "align", "broll", "subtitles", "render"))
    )

    enhance_pool: ThreadPoolExecutor | None = None
    enhance_future: Future | None = None
    enhance_started_at = None
    enhance_t0: float | None = None

    def _join_enhance(*, fail_stage: str) -> None:
        nonlocal enhance_future, enhance_pool
        if enhance_future is None:
            return
        try:
            enhance_future.result()
            if enhance_t0 is not None and enhance_started_at is not None:
                # Record wall time from submit → join when enhance was deferred.
                if not any(s.name == "enhance" for s in report.stages):
                    report.stages.append(
                        StageResult(
                            name="enhance",
                            status="ok",
                            started_at=enhance_started_at,
                            finished_at=utc_now(),
                            duration_seconds=round(time.monotonic() - enhance_t0, 3),
                        )
                    )
        except Exception as e:
            err_text = _stage_error_message(project_dir, "enhance", e)
            if enhance_t0 is not None and enhance_started_at is not None:
                report.stages.append(
                    StageResult(
                        name="enhance",
                        status="failed",
                        started_at=enhance_started_at,
                        finished_at=utc_now(),
                        duration_seconds=round(time.monotonic() - enhance_t0, 3),
                        error=err_text,
                    )
                )
            report.errors.append(f"enhance: {err_text}")
            report.status = JobStatus.failed
            report.finished_at = utc_now()
            report.broll = _load_broll_report(project_dir)
            report.veo_clips_used = sum(1 for b in report.broll if b.source == "veo")
            report.artifacts = list_artifacts(project_dir)
            save_run_report(project_dir, report)
            update_manifest(
                project_dir,
                status=JobStatus.failed,
                current_stage=fail_stage,
                error=err_text,
                artifacts=report.artifacts,
            )
            raise RuntimeError(err_text) from e
        finally:
            enhance_future = None
            if enhance_pool is not None:
                enhance_pool.shutdown(wait=False, cancel_futures=False)
                enhance_pool = None

    try:
        with observe(
            "pipeline.run",
            as_type="span",
            input={
                "from_stage": from_stage,
                "to_stage": to_stage,
                "mode": mode.value,
            },
            metadata={
                "project_id": project_name,
                "job_id": job_id,
                "category": (
                    "reel_rerender"
                    if from_stage == "render" and to_stage == "render"
                    else ("broll_generate" if to_stage == "broll" else "pipeline")
                ),
                "overlap_enhance": can_overlap,
            },
        ):
            if can_overlap:
                print("[pipeline] PIPELINE_OVERLAP_ENHANCE=1 — enhance runs beside plan/broll")

            for name in stages_slice:
                update_manifest(project_dir, current_stage=name)
                t0 = time.monotonic()
                started = utc_now()
                try:
                    stage_category = (
                        "reel_rerender"
                        if name == "render" and from_stage == "render"
                        else ("broll_generate" if name == "broll" else name)
                    )
                    with observe(
                        f"stage.{name}",
                        as_type="span",
                        metadata={"stage": name, "category": stage_category},
                    ):
                        if name == "enhance" and can_overlap:
                            enhance_pool = ThreadPoolExecutor(
                                max_workers=1, thread_name_prefix="enhance-bg"
                            )
                            enhance_started_at = started
                            enhance_t0 = t0
                            enhance_future = enhance_pool.submit(run_enhance_stage, project_dir)
                            print("[pipeline] enhance started in background")
                            # Don't append StageResult yet — recorded at join.
                            continue

                        if name == "render" and enhance_future is not None:
                            print("[pipeline] waiting for enhance before render…")
                            _join_enhance(fail_stage="render")

                        if name == "transcribe" and can_overlap:
                            run_transcribe_stage(project_dir, prefer_merged=True)
                        else:
                            STAGES[name](project_dir)

                    dur = time.monotonic() - t0
                    report.stages.append(
                        StageResult(
                            name=name,
                            status="ok",
                            started_at=started,
                            finished_at=utc_now(),
                            duration_seconds=round(dur, 3),
                        )
                    )
                except Exception as e:
                    dur = time.monotonic() - t0
                    err_text = _stage_error_message(project_dir, name, e)
                    report.stages.append(
                        StageResult(
                            name=name,
                            status="failed",
                            started_at=started,
                            finished_at=utc_now(),
                            duration_seconds=round(dur, 3),
                            error=err_text,
                        )
                    )
                    report.errors.append(f"{name}: {err_text}")
                    report.status = JobStatus.failed
                    report.finished_at = utc_now()
                    report.broll = _load_broll_report(project_dir)
                    report.veo_clips_used = sum(1 for b in report.broll if b.source == "veo")
                    report.artifacts = list_artifacts(project_dir)
                    save_run_report(project_dir, report)
                    update_manifest(
                        project_dir,
                        status=JobStatus.failed,
                        current_stage=name,
                        error=err_text,
                        artifacts=report.artifacts,
                    )
                    raise

            # If the slice ends before render (e.g. --to broll), still finish enhance.
            if enhance_future is not None:
                _join_enhance(fail_stage="enhance")

            report.status = JobStatus.completed
            report.finished_at = utc_now()
            report.broll = _load_broll_report(project_dir)
            report.veo_clips_used = sum(1 for b in report.broll if b.source == "veo")
            report.artifacts = list_artifacts(project_dir)
            save_run_report(project_dir, report)
            update_manifest(
                project_dir,
                status=JobStatus.completed,
                current_stage=None,
                artifacts=report.artifacts,
                error=None,
            )
            return report
    finally:
        if enhance_pool is not None:
            enhance_pool.shutdown(wait=False, cancel_futures=False)
        flush()
        clear_trace_context()


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description="AI Reel Factory pipeline")
    ap.add_argument(
        "project",
        nargs="?",
        default="project_001",
        help="Project folder name under projects/",
    )
    ap.add_argument("--from", dest="from_stage", metavar="STAGE", help="Start from this stage")
    ap.add_argument("--to", dest="to_stage", metavar="STAGE", help="Stop after this stage")
    ap.add_argument(
        "--mode",
        choices=["video_first", "teleprompter"],
        default=None,
        help="Pipeline mode (default: project.json or video_first)",
    )
    args = ap.parse_args()
    mode = PipelineMode(args.mode) if args.mode else None
    try:
        run_pipeline(args.project, args.from_stage, args.to_stage, mode=mode)
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
