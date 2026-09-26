"""Pydantic models for AI Reel Factory project/job contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineMode(str, Enum):
    video_first = "video_first"
    teleprompter = "teleprompter"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ProjectCreate(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Optional project folder name; auto-generated if omitted",
    )
    topic: Optional[str] = Field(
        default=None,
        description="Optional intent text (not spoken dialogue)",
    )
    mode: PipelineMode = PipelineMode.video_first


class Beat(BaseModel):
    type: Literal["aroll", "broll"]
    start: float = 0.0
    end_seconds: Optional[float] = None
    end: Optional[float] = None
    suggestion: str = ""
    # Word/segment anchors for lip-sync-safe align (preferred over scaling)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    start_word_index: Optional[int] = None
    end_word_index: Optional[int] = None


class EditingPlan(BaseModel):
    """Editing plan stored as final_script.json (name kept for compat)."""
    hook: Optional[dict[str, Any]] = None
    beats: list[Beat] = Field(default_factory=list)
    total_duration_seconds: float = 30.0
    cleaned_script: Optional[str] = None


class TimelineSegment(BaseModel):
    start: float
    end: float
    type: Literal["aroll", "broll", "filler"]
    suggestion: str = ""
    broll_index: Optional[int | str] = None


class Timeline(BaseModel):
    timeline: list[TimelineSegment] = Field(default_factory=list)
    filler_ranges: list[dict[str, Any]] = Field(default_factory=list)


class StageResult(BaseModel):
    name: str
    status: Literal["ok", "skipped", "failed"]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    message: Optional[str] = None
    error: Optional[str] = None


class BrollSourceEntry(BaseModel):
    broll_index: int | str
    source: Literal["pexels", "veo", "reuse", "skip", "skipped_keep_aroll"]
    suggestion: str = ""
    path: Optional[str] = None
    detail: Optional[str] = None


class BrollChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    prompt: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class BrollVersion(BaseModel):
    version: int
    prompt: str
    path: str
    message: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
    used_image: bool = False
    keyframe: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class BrollClipMeta(BaseModel):
    """Per-clip prompt + chat + version history (broll/NNN.meta.json)."""

    broll_index: int | str
    source: Literal["pexels", "veo", "reuse", "skipped_keep_aroll", "skip"] = "veo"
    suggestion: str = ""
    spoken_text: str = ""
    full_prompt: str = ""
    path: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
    trace_id: Optional[str] = None
    detail: Optional[str] = None
    chat: list[BrollChatTurn] = Field(default_factory=list)
    versions: list[BrollVersion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BrollEditRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Natural-language edit for this clip")


class RunReport(BaseModel):
    project_id: str
    mode: PipelineMode = PipelineMode.video_first
    job_id: Optional[str] = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    status: JobStatus = JobStatus.pending
    stages: list[StageResult] = Field(default_factory=list)
    broll: list[BrollSourceEntry] = Field(default_factory=list)
    veo_clips_used: int = 0
    veo_max_clips: Optional[int] = None
    models: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


class JobRequest(BaseModel):
    mode: PipelineMode = PipelineMode.video_first
    from_stage: Optional[str] = None
    to_stage: Optional[str] = None


class ProjectManifest(BaseModel):
    id: str
    mode: PipelineMode = PipelineMode.video_first
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    status: JobStatus = JobStatus.pending
    current_stage: Optional[str] = None
    last_job_id: Optional[str] = None
    topic: Optional[str] = None
    owner_id: Optional[str] = Field(
        default=None,
        description="Authenticated user id that owns this project",
    )
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None


class AuthLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, description="User email (dev auth identity)")
    name: Optional[str] = Field(default=None, description="Optional display name")


class AuthUser(BaseModel):
    id: str
    email: str
    name: Optional[str] = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser
