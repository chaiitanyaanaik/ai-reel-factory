"""
Edit a single B-roll clip: chat message → rewrite prompt (with still) → Veo regenerate.
"""
from __future__ import annotations

from pathlib import Path

from core.tracing import observe
from schemas.models import BrollClipMeta
from video_engine import broll_meta
from video_engine.broll import _veo_clip_seconds
from video_engine.prompt_safety import (
    UNSAFE_EDIT_MESSAGE,
    assert_safe_broll_edit,
    rewrite_looks_like_refusal,
)
from video_engine.veo_client import generate_video

CATEGORY_REGENERATE = "broll_regenerate"


def _format_chat_history(meta: BrollClipMeta) -> str:
    if not meta.chat:
        return "(no prior edits)"
    lines: list[str] = []
    for turn in meta.chat:
        if turn.role == "user":
            lines.append(f"User: {turn.content}")
        else:
            note = turn.content
            if turn.prompt:
                note = f"{note}\n  (resulting prompt: {turn.prompt[:400]})"
            lines.append(f"Assistant: {note}")
    return "\n".join(lines)


def rewrite_prompt_for_edit(
    meta: BrollClipMeta,
    user_message: str,
    keyframe_path: Path | None,
) -> str:
    """Gemini: prior prompt + chat + still + new message → new Veo prompt."""
    from script_engine.llm import gemini_text

    prompt = (
        "You revise a Veo video-generation prompt for one B-roll cutaway.\n"
        "Keep the same subject and framing unless the user asks to change them.\n"
        "Apply the user's latest edit. Output ONLY the full new prompt, no explanation.\n"
        "Safety: never produce sexual, nude, pornographic, erotic, gory, hateful, "
        "abusive, or otherwise obscene content. If the user's edit asks for that, "
        "reply with exactly: UNSAFE_EDIT\n\n"
        f"Current Veo prompt:\n{meta.full_prompt or meta.suggestion}\n\n"
        f"Original timeline suggestion:\n{meta.suggestion}\n\n"
        f"Spoken line context:\n{meta.spoken_text or '(none)'}\n\n"
        f"Prior edit chat for this clip:\n{_format_chat_history(meta)}\n\n"
        f"Latest user edit:\n{user_message.strip()}\n"
    )
    return gemini_text(
        prompt,
        image_path=keyframe_path,
        span_name="gemini.broll_edit_rewrite",
    ).strip()


def edit_broll_clip(
    project_dir: Path,
    index: int,
    message: str,
) -> BrollClipMeta:
    """
    Pick clip N, apply natural-language edit, regenerate only that clip.
    Archives previous mp4, appends chat + version to meta.
    """
    idx = int(index)
    message = (message or "").strip()
    if not message:
        raise ValueError("message is required")
    assert_safe_broll_edit(message)

    video = broll_meta.clip_path(project_dir, idx)
    if not video.exists():
        raise FileNotFoundError(f"B-roll clip not found: {video.name}")

    meta = broll_meta.load_meta(project_dir, idx)
    if meta is None:
        meta = BrollClipMeta(
            broll_index=idx,
            source="veo",
            suggestion="",
            full_prompt="",
            path=video.name,
        )

    prior_prompt = (meta.full_prompt or meta.suggestion or "")[:800]
    next_version = len(meta.versions) + 1

    with observe(
        "broll.regenerate",
        as_type="span",
        input={"broll_index": idx, "message": message},
        metadata={
            "broll_index": idx,
            "category": CATEGORY_REGENERATE,
            "operation": "regenerate",
            "version": next_version,
            "prior_prompt": prior_prompt,
        },
    ) as span:
        keyframe = project_dir / "broll" / f"{idx:03d}.keyframe.jpg"
        try:
            broll_meta.extract_keyframe(video, keyframe, at_seconds=0.5)
        except Exception as e:
            print(f"  [B-roll edit] Keyframe failed ({e}); continuing without still")
            keyframe = None

        new_prompt = rewrite_prompt_for_edit(
            meta, message, keyframe if keyframe and keyframe.exists() else None
        )
        if not new_prompt:
            raise RuntimeError("Prompt rewrite returned empty")
        if (
            new_prompt.strip().upper() == "UNSAFE_EDIT"
            or rewrite_looks_like_refusal(new_prompt)
        ):
            raise ValueError(UNSAFE_EDIT_MESSAGE)

        broll_meta.archive_current_clip(project_dir, idx)

        result = generate_video(
            meta.suggestion or new_prompt,
            video,
            duration_seconds=_veo_clip_seconds(),
            aspect_ratio="9:16",
            project_dir=project_dir,
            spoken_line=meta.spoken_text or None,
            image_path=keyframe if keyframe and Path(keyframe).exists() else None,
            full_prompt_override=new_prompt,
            return_result=True,
        )
        assert not isinstance(result, Path)

        assistant_note = f"Updated prompt for edit: {message[:120]}"
        updated = broll_meta.append_edit_result(
            project_dir,
            index=idx,
            user_message=message,
            assistant_note=assistant_note,
            full_prompt=result.full_prompt,
            model=result.model,
            latency_ms=result.latency_ms,
            estimated_cost_usd=result.estimated_cost_usd,
            used_image=result.used_image,
            keyframe_path=keyframe.name if keyframe and Path(keyframe).exists() else None,
        )
        # Refresh studio thumbnail for the new clip.
        poster = broll_meta.poster_path(project_dir, idx)
        if poster.exists():
            poster.unlink(missing_ok=True)
        broll_meta.ensure_poster(project_dir, idx)
        span.update(
            output={
                "path": updated.path,
                "versions": len(updated.versions),
                "new_prompt": (result.full_prompt or "")[:800],
            },
            metadata={
                "category": CATEGORY_REGENERATE,
                "used_image": result.used_image,
                "latency_ms": result.latency_ms,
                "version": len(updated.versions),
            },
            cost_usd=result.estimated_cost_usd,
        )
        return updated
