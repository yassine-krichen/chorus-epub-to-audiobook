"""Business logic for job creation, listing, detail, and cancellation.

Kept out of routes.py so it can be called from any transport (HTTP today,
a queue consumer or cron tomorrow) without dragging FastAPI along.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import google.auth.exceptions
import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book2audio.api.jobs import cloud_run
from book2audio.api.jobs.filenames import render_filename
from book2audio.api.jobs.schemas import (
    ChapterProgress,
    CreateJobRequest,
    JobDetail,
    JobListResponse,
    JobSummary,
    PartProgress,
)
from book2audio.db.models import Job
from book2audio.storage.r2 import presign_get

# Beyond this, a "running" job with no heartbeat is treated as possibly stuck
# and reconciled against the real Cloud Run execution state before responding.
_STALE_HEARTBEAT = timedelta(minutes=15)


def _cloud_run_job_name() -> str:
    name = os.environ.get("CLOUD_RUN_JOB_NAME")
    if not name:
        raise RuntimeError("CLOUD_RUN_JOB_NAME environment variable is not set")
    return name


def worker_mode() -> str:
    """Which render worker triggers jobs: Cloud Run (default) or a local daemon.

    Hybrid deployments run the render worker on the user's own machine, polling
    the DB instead of being triggered here -- see ``worker.local_daemon``.
    """
    mode = os.environ.get("WORKER_MODE", "cloud_run")
    if mode not in ("cloud_run", "local"):
        raise RuntimeError(
            f"WORKER_MODE must be 'cloud_run' or 'local', got {mode!r}"
        )
    return mode


def hosted_llm_attribution_enabled() -> bool:
    """Whether hosted jobs may use the LLM speaker attributor (default: no).

    Registration is open and every LLM job spends the operator's freellmapi key
    and sends book text to that gateway, so the API fails closed: only the exact
    value ``HOSTED_LLM_ATTRIBUTION=enabled`` opens it. Any other non-empty value
    is treated as a typo and raises, rather than silently staying closed (or
    open) without the operator noticing. Validated at startup like WORKER_MODE.
    """
    value = os.environ.get("HOSTED_LLM_ATTRIBUTION", "")
    if value == "":
        return False
    if value != "enabled":
        raise RuntimeError(
            f"HOSTED_LLM_ATTRIBUTION must be unset or exactly 'enabled', got {value!r}"
        )
    return True


def create_job(db: Session, user_id: uuid.UUID, payload: CreateJobRequest) -> Job:
    if payload.config.attributor != "heuristic" and not hosted_llm_attribution_enabled():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "LLM speaker attribution is not enabled on this server; use the heuristic attributor",
        )
    job = Job(
        user_id=user_id,
        title=payload.title,
        epub_object_key=payload.epub_object_key,
        config=payload.config.model_dump(),
        status="queued",
        chapters=[],
        parts=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if worker_mode() == "local":
        # A local daemon polls for "queued" jobs itself -- nothing to trigger.
        return job

    try:
        execution = _trigger_render(job)
    except (httpx.HTTPError, google.auth.exceptions.GoogleAuthError):
        job.status = "failed"
        job.error_message = "failed to start render job"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Failed to start render job"
        ) from None

    job.cloud_run_execution_name = execution
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.last_heartbeat_at = job.started_at
    db.commit()
    db.refresh(job)
    return job


def _trigger_render(job: Job) -> str | None:
    env_overrides = {
        "JOB_ID": str(job.id),
        "EPUB_OBJECT_KEY": job.epub_object_key,
        "JOB_CONFIG": json.dumps(job.config),
    }
    operation = cloud_run.run_job_execution(_cloud_run_job_name(), env_overrides)
    return operation.get("metadata", {}).get("name")


def list_jobs(
    db: Session, user_id: uuid.UUID, page: int, page_size: int
) -> JobListResponse:
    base_query = select(Job).where(Job.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    jobs = db.scalars(
        base_query.order_by(Job.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    for job in jobs:
        _reconcile_if_stale(db, job)

    return JobListResponse(
        jobs=[_to_summary(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    if job is None:
        return None
    _reconcile_if_stale(db, job)
    return job


def cancel_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    if job is None:
        return None
    if job.status in ("succeeded", "failed", "canceled"):
        return job

    if worker_mode() == "local":
        # Set unconditionally, not just for "running" jobs: a job that's still
        # "queued" right now can be claimed by the local daemon a moment later
        # (see local_daemon.py::_claim_next_job) -- without this, that race
        # lets the daemon render a job the API already canceled, with no way
        # to notice mid-render.
        job.cancel_requested_at = datetime.now(timezone.utc)
    elif job.status == "running" and job.cloud_run_execution_name:
        try:
            cloud_run.cancel_execution(job.cloud_run_execution_name)
        except (httpx.HTTPError, google.auth.exceptions.GoogleAuthError):
            pass  # best-effort: still mark canceled locally so the user isn't blocked

    job.status = "canceled"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def _reconcile_if_stale(db: Session, job: Job) -> None:
    if job.status != "running":
        return
    heartbeat = job.last_heartbeat_at
    if heartbeat is None:
        return
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - heartbeat < _STALE_HEARTBEAT:
        return

    if job.cloud_run_execution_name is None:
        if worker_mode() == "local":
            job.status = "failed"
            job.error_message = (
                "local worker did not report progress in time -- it may have crashed"
            )
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(job)
        return

    try:
        execution = cloud_run.get_execution(job.cloud_run_execution_name)
    except (httpx.HTTPError, google.auth.exceptions.GoogleAuthError):
        return  # transient API failure: leave the row as-is, retry next read

    if not cloud_run.execution_is_terminal(execution):
        return
    job.status = "succeeded" if cloud_run.execution_succeeded(execution) else "failed"
    if job.status == "failed" and not job.error_message:
        job.error_message = "Worker execution ended without reporting a result"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)


def _to_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        title=job.title,
        status=job.status,
        total_chapters=job.total_chapters,
        progress_percent=_progress_percent(job),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        artifacts_swept_at=job.artifacts_swept_at,
    )


def to_detail(job: Job) -> JobDetail:
    parts = [_to_part_progress(p, job) for p in job.parts]
    return JobDetail(
        **_to_summary(job).model_dump(),
        epub_object_key=job.epub_object_key,
        config=job.config,
        chapters=[_to_chapter_progress(c, job) for c in job.chapters],
        parts=parts,
        error_message=job.error_message,
    )


def _to_part_progress(part: dict, job: Job) -> PartProgress:
    download_url = None
    if part.get("status") == "done" and job.artifacts_swept_at is None:
        template = job.config.get("filename_template")
        download_filename = (
            render_filename(template, title=job.title, part=part["part_number"])
            if template
            else None
        )
        download_url = presign_get(part["r2_key"], download_filename=download_filename)
    return PartProgress(**part, download_url=download_url)


def _to_chapter_progress(chapter: dict, job: Job) -> ChapterProgress:
    thumbnail_url = None
    if (
        job.artifacts_swept_at is None
        and chapter.get("thumbnail_status") == "ready"
        and chapter.get("thumbnail_r2_key")
    ):
        thumbnail_url = presign_get(chapter["thumbnail_r2_key"])
    return ChapterProgress(
        index=chapter["index"],
        title=chapter["title"],
        status=chapter["status"],
        thumbnail_status=chapter.get("thumbnail_status"),
        thumbnail_url=thumbnail_url,
    )


def _progress_percent(job: Job) -> float:
    if not job.total_chapters:
        return 0.0
    done = sum(1 for c in job.chapters if c.get("status") == "done")
    return round(100 * done / job.total_chapters, 1)
