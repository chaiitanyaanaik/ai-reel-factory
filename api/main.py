"""FastAPI application for AI Reel Factory / ReelKut."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.auth import (
    CurrentUser,
    auth_mode,
    auth_required,
    cookie_secure,
    get_current_user,
    issue_dev_token,
    issue_media_token,
    require_project_owner,
    validate_auth_config,
)
from core.config import load_env
from core.project_store import (
    create_project,
    get_project_dir,
    list_artifacts,
    list_clips,
    list_projects,
    load_manifest,
    load_run_report,
    reorder_clips,
)
from core.usage_limits import assert_under_limit, check_and_increment, usage_snapshot
from jobs import runner
from schemas.models import (
    AuthLoginRequest,
    AuthTokenResponse,
    AuthUser,
    BrandProfile,
    BrollEditRequest,
    JobRequest,
    ProjectCreate,
    ProjectManifest,
)

load_env()

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("ai_reel_factory.api")

app = FastAPI(
    title="ReelKut / AI Reel Factory",
    description="Video-first Instagram Reel pipeline API (lip-sync safe, multi-user).",
    version="1.1.0",
)

_cors = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",") if o.strip()] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup_auth_guard() -> None:
    try:
        validate_auth_config()
    except RuntimeError as e:
        logger.error("Unsafe auth configuration: %s", e)
        raise


class JobAccepted(BaseModel):
    job_id: str
    project_id: str
    status: str


class ClipOrderRequest(BaseModel):
    order: list[str] = Field(..., min_length=1, description="Current filenames in desired sequence")


def _owned_project(project_id: str, user: CurrentUser) -> Path:
    try:
        project_dir = get_project_dir(project_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    manifest = load_manifest(project_dir)
    require_project_owner(manifest.owner_id, user)
    return project_dir


@app.get("/health")
def health():
    return {
        "status": "ok",
        "auth_mode": auth_mode(),
        "auth_required": auth_required(),
    }


@app.get("/auth/usage")
def auth_usage(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return usage_snapshot(user.id)


@app.get("/auth/me", response_model=AuthUser)
def auth_me(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return AuthUser(id=user.id, email=user.email, name=user.name)


@app.get("/auth/brand", response_model=BrandProfile)
def auth_get_brand(user: Annotated[CurrentUser, Depends(get_current_user)]):
    from core.user_brand import load_user_brand

    return load_user_brand(user.id)


@app.put("/auth/brand", response_model=BrandProfile)
def auth_put_brand(
    body: BrandProfile,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    from core.user_brand import save_user_brand

    try:
        return save_user_brand(user.id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/auth/media-token")
def auth_media_token(user: Annotated[CurrentUser, Depends(get_current_user)]):
    """
    Short-lived token for media URLs (?access_token=). Prefer cookie when same-origin.
    Do not put the long-lived session JWT in <video src> query strings.
    """
    token, ttl = issue_media_token(user)
    return {"access_token": token, "expires_in": ttl, "token_type": "media"}


@app.post("/auth/login", response_model=AuthTokenResponse)
def auth_login(body: AuthLoginRequest, response: Response):
    """
    Dev auth: email is the identity (no password).
    Set AUTH_MODE=clerk and use Clerk tokens in production instead.
    """
    if auth_mode() == "clerk":
        raise HTTPException(
            status_code=400,
            detail="AUTH_MODE=clerk — sign in via Clerk on the frontend, then send Bearer token",
        )
    if auth_mode() in ("off", "none", "disabled") and not auth_required():
        # Still allow login so UI can stamp owner_id on new projects.
        pass
    try:
        token, user = issue_dev_token(email=body.email, name=body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    response.set_cookie(
        key="reelkut_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        max_age=int(os.environ.get("AUTH_TOKEN_TTL_SECONDS", "604800") or 604800),
    )
    return AuthTokenResponse(
        access_token=token,
        user=AuthUser(id=user.id, email=user.email, name=user.name),
    )


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(
        "reelkut_token",
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    return {"ok": True}


@app.get("/projects")
def api_list_projects(user: Annotated[CurrentUser, Depends(get_current_user)]):
    if auth_required():
        return {"projects": list_projects(owner_id=user.id)}
    return {"projects": list_projects(owner_id=None)}


@app.post("/projects", response_model=ProjectManifest)
def api_create_project(
    body: ProjectCreate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    assert_under_limit(user.id, "project_create")
    try:
        manifest = create_project(
            name=body.name,
            topic=body.topic,
            mode=body.mode,
            owner_id=user.id,
        )
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    check_and_increment(user.id, "project_create")
    return manifest


@app.get("/projects/{project_id}")
def api_get_project(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    manifest = load_manifest(project_dir)
    report = load_run_report(project_dir)
    artifacts = list_artifacts(project_dir)
    clips = list_clips(project_dir)
    return {
        "manifest": manifest.model_dump(mode="json"),
        "artifacts": artifacts,
        "clips": clips,
        "run_report": report.model_dump(mode="json") if report else None,
        "artifact_urls": {
            k: f"/projects/{project_id}/artifacts/{k}"
            for k in ("reel", "cover", "script")
            if k in artifacts
        },
        "error": manifest.error,
    }


@app.get("/projects/{project_id}/clips")
def api_list_clips(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    return {"project_id": project_id, "clips": list_clips(project_dir)}


@app.put("/projects/{project_id}/clips/order")
def api_reorder_clips(
    project_id: str,
    body: ClipOrderRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    try:
        clips = reorder_clips(project_dir, body.order)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"project_id": project_id, "clips": clips}


@app.post("/projects/{project_id}/clips")
async def api_upload_clips(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    files: list[UploadFile] = File(...),
    replace: bool = Query(False),
):
    """
    Upload clips in request order → saved as 01.ext, 02.ext, ...
    Set replace=true to clear existing raw_clips first.
    """
    project_dir = _owned_project(project_id, user)

    clips_dir = project_dir / "raw_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    if replace:
        for p in list(clips_dir.glob("*.mp4")) + list(clips_dir.glob("*.mov")):
            p.unlink(missing_ok=True)

    existing = list_clips(project_dir)
    start = 1 if replace or not existing else (existing[-1]["index"] + 1)

    saved = []
    for offset, upload in enumerate(files):
        i = start + offset
        original = upload.filename or f"{i:02d}.mp4"
        suffix = Path(original).suffix.lower() or ".mp4"
        if suffix not in (".mp4", ".mov"):
            suffix = ".mp4"
        name = f"{i:02d}{suffix}"
        dest = clips_dir / name
        data = await upload.read()
        dest.write_bytes(data)
        saved.append(name)

    return {
        "project_id": project_id,
        "saved": saved,
        "clips": list_clips(project_dir),
    }


@app.get("/projects/{project_id}/clips/{filename}")
def api_clip_file(
    project_id: str,
    filename: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    safe = Path(filename).name
    path = project_dir / "raw_clips" / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    media = "video/quicktime" if path.suffix.lower() == ".mov" else "video/mp4"
    return FileResponse(path, media_type=media, filename=safe)


@app.get("/projects/{project_id}/script", response_class=PlainTextResponse)
def api_get_script(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    path = project_dir / "raw_script.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Script not extracted yet")
    return path.read_text(encoding="utf-8")


@app.post("/projects/{project_id}/jobs", response_model=JobAccepted)
def api_start_job(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    body: JobRequest | None = None,
):
    body = body or JobRequest()
    _owned_project(project_id, user)
    check_and_increment(user.id, "job")
    record = runner.submit(
        project_id,
        body,
        user_id=user.id,
        user_email=user.email,
    )
    return JobAccepted(
        job_id=record.job_id,
        project_id=project_id,
        status=record.status.value,
    )


@app.get("/projects/{project_id}/broll")
def api_list_broll(
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """List every B-roll clip with prompt/chat/version summary."""
    project_dir = _owned_project(project_id, user)
    from video_engine.broll_meta import list_broll_metas

    metas = list_broll_metas(project_dir)
    from video_engine.broll_meta import clip_path

    return {
        "project_id": project_id,
        "broll": [
            {
                **m.model_dump(mode="json"),
                "video_url": (
                    f"/projects/{project_id}/broll/{int(m.broll_index)}/video"
                    if clip_path(project_dir, int(m.broll_index)).exists()
                    else None
                ),
            }
            for m in metas
        ],
    }


@app.get("/projects/{project_id}/broll/{index}")
def api_get_broll(
    project_id: str,
    index: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    from video_engine.broll_meta import clip_path, load_meta

    meta = load_meta(project_dir, index)
    video = clip_path(project_dir, index)
    if meta is None and not video.exists():
        raise HTTPException(status_code=404, detail=f"B-roll {index:03d} not found")
    if meta is None:
        from schemas.models import BrollClipMeta

        meta = BrollClipMeta(
            broll_index=index,
            source="reuse",
            path=video.name,
        )
    return {
        **meta.model_dump(mode="json"),
        "video_url": f"/projects/{project_id}/broll/{index}/video" if video.exists() else None,
    }


@app.get("/projects/{project_id}/broll/{index}/video")
def api_broll_video(
    project_id: str,
    index: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    from video_engine.broll_meta import clip_path

    path = clip_path(project_dir, index)
    if not path.exists():
        raise HTTPException(status_code=404, detail="B-roll video not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.post("/projects/{project_id}/broll/{index}/edit")
def api_edit_broll(
    project_id: str,
    index: int,
    body: BrollEditRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """
    Natural-language edit for one B-roll: rewrite prompt (with still + chat) → regenerate.
    """
    project_dir = _owned_project(project_id, user)
    check_and_increment(user.id, "broll_edit")
    from core.tracing import clear_trace_context, flush, set_trace_context
    from video_engine.broll_edit import edit_broll_clip

    set_trace_context(
        user_id=user.id,
        user_email=user.email,
        project_id=project_id,
        job_id=f"edit-{index:03d}",
    )
    try:
        meta = edit_broll_clip(project_dir, index, body.message)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("B-roll edit failed for %s/%s", project_id, index)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        flush()
        clear_trace_context()

    return {
        **meta.model_dump(mode="json"),
        "video_url": f"/projects/{project_id}/broll/{index}/video",
    }


@app.get("/projects/{project_id}/jobs/{job_id}")
def api_get_job(
    project_id: str,
    job_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _owned_project(project_id, user)
    record = runner.get(job_id)
    if record is None or record.project_id != project_id:
        try:
            project_dir = get_project_dir(project_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        report = load_run_report(project_dir)
        if report and report.job_id == job_id:
            return {
                "job_id": job_id,
                "project_id": project_id,
                "status": report.status.value,
                "error": report.errors[-1] if report.errors else None,
                "run_report": report.model_dump(mode="json"),
            }
        raise HTTPException(status_code=404, detail="Job not found")
    payload = {
        "job_id": job_id,
        "project_id": project_id,
        "status": record.status.value,
        "error": record.error,
    }
    try:
        report = load_run_report(get_project_dir(project_id))
        if report:
            payload["run_report"] = report.model_dump(mode="json")
    except FileNotFoundError:
        pass
    return payload


@app.get("/projects/{project_id}/artifacts/{name}")
def api_artifact(
    project_id: str,
    name: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    project_dir = _owned_project(project_id, user)
    artifacts = list_artifacts(project_dir)
    if name not in artifacts:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {name}")
    path = project_dir / artifacts[name]
    if path.suffix == ".md":
        return PlainTextResponse(path.read_text(encoding="utf-8"))
    media = "video/mp4" if path.suffix == ".mp4" else "application/octet-stream"
    if path.suffix in (".jpg", ".jpeg"):
        media = "image/jpeg"
    return FileResponse(path, media_type=media, filename=path.name)


def main():
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
