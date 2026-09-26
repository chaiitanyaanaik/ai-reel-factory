"""Background job runner for pipeline stages (single-machine)."""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from core.project_store import get_project_dir, load_manifest, load_run_report, update_manifest
from pipeline import run_pipeline
from schemas.models import JobRequest, JobStatus, RunReport

logger = logging.getLogger("ai_reel_factory.jobs")


@dataclass
class JobRecord:
    job_id: str
    project_id: str
    request: JobRequest
    status: JobStatus = JobStatus.pending
    error: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    future: Optional[Future] = field(default=None, repr=False)


class JobRunner:
    """In-process thread pool job runner (swap for Celery/RQ later)."""

    def __init__(self, max_workers: int = 1):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="reel-job")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        project_id: str,
        request: JobRequest,
        *,
        user_id: str | None = None,
        user_email: str | None = None,
    ) -> JobRecord:
        # Ensure project exists
        get_project_dir(project_id)
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(
            job_id=job_id,
            project_id=project_id,
            request=request,
            user_id=user_id,
            user_email=user_email,
        )
        with self._lock:
            self._jobs[job_id] = record

        def _run() -> RunReport:
            record.status = JobStatus.running
            update_manifest(
                get_project_dir(project_id),
                status=JobStatus.running,
                last_job_id=job_id,
                mode=request.mode,
            )
            try:
                report = run_pipeline(
                    project_id,
                    from_stage=request.from_stage,
                    to_stage=request.to_stage,
                    mode=request.mode,
                    job_id=job_id,
                    user_id=user_id,
                    user_email=user_email,
                )
                record.status = JobStatus.completed
                return report
            except Exception as e:
                logger.exception("Job %s failed", job_id)
                record.status = JobStatus.failed
                record.error = str(e)
                raise

        record.future = self._executor.submit(_run)
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def project_status(self, project_id: str) -> dict:
        project_dir = get_project_dir(project_id)
        manifest = load_manifest(project_dir)
        report = load_run_report(project_dir)
        return {
            "manifest": manifest.model_dump(mode="json"),
            "run_report": report.model_dump(mode="json") if report else None,
        }


# Module-level singleton for the API
runner = JobRunner(max_workers=1)
