"""How long a job may wait for its cast review, and what happens when that runs out.

Shared by the API (which expires an overdue review when the job is read) and the cleanup
sweeper (which expires the ones nobody read, so their stored files are cleaned up too).
Both go through :func:`expire_if_lapsed`, a conditional update, so an expiry can never
overwrite a confirmation that lands at the same moment.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from book2audio.cast_review import AWAITING_CAST_REVIEW
from book2audio.db.models import Job

_DEFAULT_TIMEOUT_HOURS = 48
_MAX_TIMEOUT_HOURS = 24 * 30

LAPSED_MESSAGE = (
    "The cast review was not confirmed in time, so nothing was rendered. "
    "Create the job again to start over."
)


def cast_review_timeout() -> timedelta:
    """How long a job may wait for its cast review before it is canceled.

    ``CAST_REVIEW_TIMEOUT_HOURS`` (default 48). A bad value raises, and the API
    checks it at startup like ``WORKER_MODE``, so a typo cannot silently mean
    "never expires" or "expires at once".
    """
    raw = os.environ.get("CAST_REVIEW_TIMEOUT_HOURS", "")
    if raw == "":
        return timedelta(hours=_DEFAULT_TIMEOUT_HOURS)
    try:
        hours = int(raw)
    except ValueError:
        hours = 0
    if not 1 <= hours <= _MAX_TIMEOUT_HOURS:
        raise RuntimeError(
            "CAST_REVIEW_TIMEOUT_HOURS must be a whole number of hours from 1 to "
            f"{_MAX_TIMEOUT_HOURS}, got {raw!r}"
        )
    return timedelta(hours=hours)


def expires_at(job: Job) -> datetime | None:
    """When a job waiting for its cast review is canceled if still unconfirmed."""
    if job.status != AWAITING_CAST_REVIEW:
        return None
    started = job.cast_review_requested_at or job.last_heartbeat_at or job.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started + cast_review_timeout()


def expire_if_lapsed(db: Session, job: Job) -> bool:
    """Cancel ``job`` if its review window is over. Returns whether this call did it."""
    deadline = expires_at(job)
    if deadline is None or datetime.now(timezone.utc) < deadline:
        return False
    # Conditional on the status: a confirmation that landed first wins.
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == AWAITING_CAST_REVIEW)
        .values(
            status="canceled",
            error_message=LAPSED_MESSAGE,
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(job)
    return result.rowcount == 1
