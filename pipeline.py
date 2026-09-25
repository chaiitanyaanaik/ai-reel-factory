"""
AI Reel Factory — main workflow orchestrator.

Runs locally; each stage is modular and can be replaced or skipped.
Optional: USE_FILLER_BROLL=1 for um/ah B-roll overlay + mute (off by default).
"""
from pathlib import Path
import argparse
import sys

# Project root — ensure imports work when run from any directory
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_project_dir(name: str) -> Path:
    p = ROOT / "projects" / name
    if not p.is_dir():
        raise FileNotFoundError(f"Project not found: {p}")
    return p


def run_script_stage(project_dir: Path) -> None:
    """1. Read topic -> generate script -> editing plan."""
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
    print("[OK] Script + editing plan")


def run_merge_stage(project_dir: Path) -> Path:
    """2. Merge raw_clips -> merged/merged.mp4."""
    from video_engine.merge_clips import merge_clips
    path = merge_clips(project_dir)
    print(f"[OK] Merged -> {path}")
    return path


def run_transcribe_stage(project_dir: Path) -> Path:
    """3. Whisper on merged video -> transcripts/transcript.json."""
    merged = project_dir / "merged" / "merged.mp4"
    if not merged.exists():
        raise FileNotFoundError(f"Run merge first: {merged}")
    from video_engine.transcribe import transcribe, save_transcript
    data = transcribe(merged)
    path = save_transcript(project_dir, data)
    print(f"[OK] Transcript -> {path}")
    return path


def run_filler_stage(project_dir: Path) -> Path:
    """4. Filler detection -> cuts/filler_segments.json (for B-roll + mute)."""
    from video_engine.filler_detection import load_transcript, get_filler_segments, save_filler_segments
    transcript = load_transcript(project_dir)
    segments = get_filler_segments(transcript)
    path = save_filler_segments(project_dir, segments)
    print(f"[OK] Filler segments -> {path} ({len(segments)} segments)")
    return path


def run_align_stage(project_dir: Path) -> Path:
    """4. Align editing plan to transcript -> cuts/timeline.json (optional fillers via USE_FILLER_BROLL=1)."""
    from video_engine.timeline import build_timeline_with_fillers, save_timeline
    timeline, filler_ranges = build_timeline_with_fillers(project_dir)
    path = save_timeline(project_dir, timeline, filler_ranges)
    print(f"[OK] Timeline -> {path} ({len(timeline)} segments)")
    return path


def run_broll_stage(project_dir: Path) -> None:
    """5. Source B-roll clips: Pexels (free) first, Veo fallback. Respects env toggles."""
    from video_engine.broll import generate_broll_from_timeline, prepare_broll_placeholders
    try:
        generate_broll_from_timeline(project_dir)
        print("[OK] B-roll sourced")
    except Exception as e:
        print(f"[ERROR] B-roll generation failed: {e}")
        prepare_broll_placeholders(project_dir)
        print("[SKIP] B-roll: using placeholders. Reel will still render.")


def run_subtitles_stage(project_dir: Path) -> Path:
    """6. Generate subtitles from transcript (fillers stripped in captions)."""
    from video_engine.subtitles import generate_srt
    path = generate_srt(project_dir, strip_fillers=True)
    print(f"[OK] Subtitles -> {path}")
    return path


def run_render_stage(project_dir: Path) -> Path:
    """7. Final reel: A/B-roll overlays, boosted speaker audio, 1080x1920 -> final/reel.mp4."""
    from video_engine.render import render_final
    path = render_final(project_dir, width=1080, height=1920)
    print(f"[OK] Final reel -> {path}")
    return path


def run_cover_stage(project_dir: Path) -> Path:
    """8. Generate AI cover image from rendered reel + script artifacts."""
    from video_engine.cover import generate_cover_from_reel
    path = generate_cover_from_reel(project_dir)
    print(f"[OK] Cover -> {path}")
    return path


STAGES = {
    "script": run_script_stage,
    "merge": run_merge_stage,
    "transcribe": run_transcribe_stage,
    "filler": run_filler_stage,
    "align": run_align_stage,
    "broll": run_broll_stage,
    "subtitles": run_subtitles_stage,
    "render": run_render_stage,
    "cover": run_cover_stage,
}


def run_pipeline(project_name: str, from_stage: str | None = None, to_stage: str | None = None) -> None:
    order = ["script", "merge", "transcribe", "align", "broll", "subtitles", "render", "cover"]
    if from_stage:
        try:
            start = order.index(from_stage)
        except ValueError:
            print(f"Unknown stage: {from_stage}. Use: {order}")
            sys.exit(1)
    else:
        start = 0
    if to_stage:
        try:
            end = order.index(to_stage) + 1
        except ValueError:
            print(f"Unknown stage: {to_stage}. Use: {order}")
            sys.exit(1)
    else:
        end = len(order)
    project_dir = get_project_dir(project_name)
    for name in order[start:end]:
        STAGES[name](project_dir)


def main():
    ap = argparse.ArgumentParser(description="AI Reel Factory pipeline")
    ap.add_argument("project", nargs="?", default="project_001", help="Project folder name under projects/")
    ap.add_argument("--from", dest="from_stage", metavar="STAGE", help="Start from this stage")
    ap.add_argument("--to", dest="to_stage", metavar="STAGE", help="Stop after this stage")
    args = ap.parse_args()
    run_pipeline(args.project, args.from_stage, args.to_stage)


if __name__ == "__main__":
    main()
