"""Business logic for job creation, listing, detail, and cancellation.

Kept out of routes.py so it can be called from any transport (HTTP today,
a queue consumer or cron tomorrow) without dragging FastAPI along.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import google.auth.exceptions
import httpx
from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from book2audio.api.jobs import cloud_run
from book2audio.api.jobs.schemas import (
    CastReport,
    CastReviewRequest,
    ChapterProgress,
    CreateJobRequest,
    JobConfig,
    JobDetail,
    JobListResponse,
    JobSummary,
    PartProgress,
)
from book2audio.assemble.filenames import (
    PartInfo,
    default_filename_template,
    render_part_filenames,
)
from book2audio.cast_report import cast_report_object_key
from book2audio.cast_review import AWAITING_CAST_REVIEW
from book2audio.db.models import Job
from book2audio.models import NARRATOR
from book2audio.storage.r2 import presign_get, read_object

logger = logging.getLogger(__name__)

# A cast report is a few KB per character; this only stops a corrupt or hostile
# object from being pulled into the API's memory.
_MAX_CAST_REPORT_BYTES = 2 * 1024 * 1024

# Beyond this, a "running" job with no heartbeat is treated as possibly stuck
# and reconciled against the real Cloud Run execution state before responding.
_STALE_HEARTBEAT = timedelta(minutes=15)

_DEFAULT_CAST_REVIEW_TIMEOUT_HOURS = 48
_MAX_CAST_REVIEW_TIMEOUT_HOURS = 24 * 30


def cast_review_timeout() -> timedelta:
    """How long a job may wait for its cast review before it is canceled.

    ``CAST_REVIEW_TIMEOUT_HOURS`` (default 48). A bad value raises, and the API
    checks it at startup like ``WORKER_MODE``, so a typo cannot silently mean
    "never expires" or "expires at once".
    """
    raw = os.environ.get("CAST_REVIEW_TIMEOUT_HOURS", "")
    if raw == "":
        return timedelta(hours=_DEFAULT_CAST_REVIEW_TIMEOUT_HOURS)
    try:
        hours = int(raw)
    except ValueError:
        hours = 0
    if not 1 <= hours <= _MAX_CAST_REVIEW_TIMEOUT_HOURS:
        raise RuntimeError(
            "CAST_REVIEW_TIMEOUT_HOURS must be a whole number of hours from 1 to "
            f"{_MAX_CAST_REVIEW_TIMEOUT_HOURS}, got {raw!r}"
        )
    return timedelta(hours=hours)


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
    # narrator_only never attributes speakers, so it never spends the LLM key.
    uses_llm = payload.config.attributor != "heuristic" and payload.config.voice_mode == "cast"
    if uses_llm and not hosted_llm_attribution_enabled():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "AI speaker detection isn't available on this server yet. "
            'Turn off "AI speaker detection" and try again.',
        )
    if payload.config.thumbnails_enhanced and not hosted_llm_attribution_enabled():
        # Same gate as AI speaker detection: it spends the operator's chat key too.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Smarter thumbnails aren't available on this server yet. "
            'Turn off "Smarter thumbnails" and try again.',
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


def get_cast_report(job: Job) -> CastReport | None:
    """The job's cast report, or ``None`` when there is none to show.

    ``None`` covers a worker that has not reached casting yet, artifacts already
    swept, and a stored file that is missing or not a valid report: the job page
    then simply has no cast panel.
    """
    if job.artifacts_swept_at is not None:
        return None
    key = cast_report_object_key(job.id)
    try:
        raw = read_object(key, max_bytes=_MAX_CAST_REPORT_BYTES)
        return None if raw is None else CastReport.model_validate_json(raw)
    except (ValueError, ValidationError):
        logger.warning("job.cast_report_invalid", extra={"job_id": str(job.id)})
        return None


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


def confirm_cast_review(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID, payload: CastReviewRequest
) -> Job | None:
    """Apply the user's cast fixes and resume the paused job.

    The job's settings gain the fixes (gender and voice choices, merges, narrator
    hand-offs) and ``cast_review`` goes off so the resumed run renders. Resuming reuses
    the attribution cache the worker stored, so no model call is paid twice.
    ``None`` means no such job for this user; a job that is not waiting (or whose review
    lapsed) is a 409, a fix naming someone who is not in the cast is a 422.
    """
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    if job is None:
        return None
    _reconcile_if_stale(db, job)  # an overdue review is canceled here, not resumed
    if job.status != AWAITING_CAST_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This job is not waiting for a cast review"
        )
    report = get_cast_report(job)
    if report is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The detected cast is no longer available. Cancel this job and create it again.",
        )
    new_config = _config_with_review(job.config, report, payload)

    local = worker_mode() == "local"
    requested_at = job.cast_review_requested_at
    # Only one confirmation can win: a second click, or the expiry, finds the status
    # already changed and updates no row.
    claimed = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == AWAITING_CAST_REVIEW)
        .values(
            config=new_config,
            status="queued" if local else "running",
            cast_review_requested_at=None,
            last_heartbeat_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This job is not waiting for a cast review"
        )
    db.refresh(job)
    if local:
        return job  # the local daemon picks the queued job up and resumes it

    try:
        execution = _trigger_render(job)
    except (httpx.HTTPError, google.auth.exceptions.GoogleAuthError):
        # Back to waiting, so the user can press the button again.
        db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "running")
            .values(status=AWAITING_CAST_REVIEW, cast_review_requested_at=requested_at)
        )
        db.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Failed to start render job"
        ) from None
    job.cloud_run_execution_name = execution
    db.commit()
    db.refresh(job)
    return job


def _config_with_review(
    config: dict, report: CastReport, payload: CastReviewRequest
) -> dict:
    """The job's settings with the review's fixes folded in, validated as a whole."""
    detected = {character.name for character in report.characters}
    aliases = dict(config.get("character_aliases", {}))
    genders = dict(config.get("gender_overrides", {}))
    voices = dict(config.get("voice_overrides", {}))

    for edit in payload.characters:
        if edit.name not in detected:
            raise HTTPException(
                422, f"{edit.name!r} is not in the detected cast"
            )
        if edit.merge_into is not None and edit.merge_into not in detected:
            raise HTTPException(
                422,
                f"{edit.merge_into!r} is not in the detected cast",
            )
        if edit.merge_into is not None or edit.read_by_narrator:
            aliases[edit.name] = edit.merge_into if edit.merge_into is not None else NARRATOR
            genders.pop(edit.name, None)
            voices.pop(edit.name, None)
            continue
        if edit.gender is not None:
            genders[edit.name] = edit.gender
        if edit.voice is not None:
            voices[edit.name] = edit.voice

    merged = {
        **config,
        "character_aliases": aliases,
        "gender_overrides": genders,
        "voice_overrides": voices,
        "cast_review": False,
    }
    try:
        return JobConfig.model_validate(merged).model_dump()
    except ValidationError as error:
        message = str(error.errors()[0]["msg"]).removeprefix("Value error, ")
        raise HTTPException(422, message) from None


def cast_review_expires_at(job: Job) -> datetime | None:
    """When a job waiting for its cast review is canceled if still unconfirmed."""
    if job.status != AWAITING_CAST_REVIEW:
        return None
    started = job.cast_review_requested_at or job.last_heartbeat_at or job.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started + cast_review_timeout()


def _expire_review_if_lapsed(db: Session, job: Job) -> None:
    expires_at = cast_review_expires_at(job)
    if expires_at is None or datetime.now(timezone.utc) < expires_at:
        return
    # Conditional, so a confirmation that lands at the same moment is not overwritten.
    db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == AWAITING_CAST_REVIEW)
        .values(
            status="canceled",
            error_message=(
                "The cast review was not confirmed in time, so nothing was rendered. "
                "Create the job again to start over."
            ),
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(job)


def _reconcile_if_stale(db: Session, job: Job) -> None:
    if job.status == AWAITING_CAST_REVIEW:
        _expire_review_if_lapsed(db, job)
        return
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
    download_filenames = _render_download_filenames(job, job.parts)
    parts = [_to_part_progress(p, job, download_filenames) for p in job.parts]
    return JobDetail(
        **_to_summary(job).model_dump(),
        epub_object_key=job.epub_object_key,
        config=job.config,
        chapters=[_to_chapter_progress(c, job) for c in job.chapters],
        parts=parts,
        error_message=job.error_message,
        cast_review_expires_at=cast_review_expires_at(job),
    )


def _render_download_filenames(job: Job, parts: list[dict]) -> dict[int, str]:
    """Download name per part number, rendered together so names stay unique.

    Empty when the job has no template (and is not one file per chapter) or
    its stored template no longer renders.
    """
    template = job.config.get("filename_template") or default_filename_template(
        job.config.get("batch_size", 10)
    )
    if not template or not parts:
        return {}
    part_infos = [
        PartInfo(
            part_number=part["part_number"],
            # Parts stored before book numbering only know the parser position.
            chapter_number=_first_chapter_number(part),
            chapter_title=part.get("first_title") or "",
        )
        for part in parts
    ]
    try:
        names = render_part_filenames(template, title=job.title, parts=part_infos)
    except ValueError:
        # Jobs stored before the width cap / bidi checks can hold a template
        # that no longer renders; serve the download under its storage name
        # rather than failing the whole job detail response.
        logger.warning("job.filename_template_invalid", extra={"job_id": str(job.id)})
        return {}
    return {info.part_number: name for info, name in zip(part_infos, names)}


def _first_chapter_number(part: dict) -> int:
    number = part.get("first_number")
    return number if number is not None else part["first_index"]


def _to_part_progress(
    part: dict, job: Job, download_filenames: dict[int, str] | None = None
) -> PartProgress:
    download_url = None
    if part.get("status") == "done" and job.artifacts_swept_at is None:
        if download_filenames is None:
            download_filenames = _render_download_filenames(job, [part])
        download_url = presign_get(
            part["r2_key"], download_filename=download_filenames.get(part["part_number"])
        )
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
        number=chapter.get("number"),
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
