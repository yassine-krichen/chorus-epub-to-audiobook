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


def create_job(db: Session, user_id: uuid.UUID, payload: CreateJobRequest) -> Job:
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

    if job.status == "running" and job.cloud_run_execution_name:
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
    if job.status != "running" or not job.cloud_run_execution_name:
        return
    heartbeat = job.last_heartbeat_at
    if heartbeat is None:
        return
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - heartbeat < _STALE_HEARTBEAT:
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
    )


def to_detail(job: Job) -> JobDetail:
    parts = [_to_part_progress(p) for p in job.parts]
    return JobDetail(
        **_to_summary(job).model_dump(),
        epub_object_key=job.epub_object_key,
        config=job.config,
        chapters=[ChapterProgress(**c) for c in job.chapters],
        parts=parts,
        error_message=job.error_message,
    )


def _to_part_progress(part: dict) -> PartProgress:
    download_url = presign_get(part["r2_key"]) if part.get("status") == "done" else None
    return PartProgress(**part, download_url=download_url)


def _progress_percent(job: Job) -> float:
    if not job.total_chapters:
        return 0.0
    done = sum(1 for c in job.chapters if c.get("status") == "done")
    return round(100 * done / job.total_chapters, 1)
