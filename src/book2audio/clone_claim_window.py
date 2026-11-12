"""How long a job that needs a cloned voice waits for a capable worker, and what happens then.

A job that names a cloned voice (``requires_clone``) is only claimed by a local worker that has
the clone engine. If no such worker is around, the job would sit queued for ever with no word to
the user, so once none has been seen for a while it fails with a message that says what to do.
While a capable worker is online the job is never failed, however long it waits: the worker
renders one job at a time and a book takes hours. Shared by the API (which fails an
overdue job when it is read) and the cleanup sweeper (which fails the ones nobody read), both
through :func:`fail_if_unclaimed`, a conditional update: a worker that claims the job at the same
moment wins.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from book2audio.db.models import Job
from book2audio.db.worker_heartbeat import last_seen_with_capability, online_capabilities
from book2audio.tts.clone_engine import CLONE_CAPABILITY

_DEFAULT_TIMEOUT_MINUTES = 30
_MAX_TIMEOUT_MINUTES = 24 * 60

UNCLAIMED_MESSAGE = (
    "No local worker with the voice-cloning engine picked this job up in time, so nothing was "
    "rendered. Start your local worker and create the job again, or choose built-in voices."
)


def clone_claim_timeout() -> timedelta:
    """How long a queued voice-cloning job may wait for a capable worker.

    ``CLONE_CLAIM_TIMEOUT_MINUTES`` (default 30). A bad value raises, and the API checks it at
    startup like ``CAST_REVIEW_TIMEOUT_HOURS``, so a typo cannot silently mean "never".
    """
    raw = os.environ.get("CLONE_CLAIM_TIMEOUT_MINUTES", "")
    if raw == "":
        return timedelta(minutes=_DEFAULT_TIMEOUT_MINUTES)
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 0
    if not 1 <= minutes <= _MAX_TIMEOUT_MINUTES:
        raise RuntimeError(
            "CLONE_CLAIM_TIMEOUT_MINUTES must be a whole number of minutes from 1 to "
            f"{_MAX_TIMEOUT_MINUTES}, got {raw!r}"
        )
    return timedelta(minutes=minutes)


def _waiting_since(job: Job) -> datetime:
    # A job put back in the queue (a confirmed cast review) restarts the wait: its heartbeat is
    # set at that moment. A new job has no heartbeat yet, so its creation time counts.
    since = job.last_heartbeat_at or job.created_at
    return since if since.tzinfo else since.replace(tzinfo=timezone.utc)


def fail_if_unclaimed(db: Session, job: Job) -> bool:
    """Fail ``job`` if it has waited too long for a capable worker. Returns whether this call did."""
    if job.status != "queued" or not job.requires_clone:
        return False
    now = datetime.now(timezone.utc)
    if CLONE_CAPABILITY in online_capabilities(db):
        # A capable worker is there. It renders one job at a time and a book takes hours, so a
        # long wait behind a busy worker is normal and not a reason to fail the job.
        return False
    # No capable worker right now: the wait that counts is the time since one was last seen, or
    # since the job started waiting, whichever is later. A worker that goes offline briefly and
    # comes back therefore never costs a job its place.
    last_capable = last_seen_with_capability(db, CLONE_CAPABILITY)
    waiting_since = max(_waiting_since(job), last_capable or _waiting_since(job))
    if now - waiting_since < clone_claim_timeout():
        return False
    # Conditional on still being queued: a worker that claimed it first keeps it.
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == "queued", Job.requires_clone.is_(True))
        .values(status="failed", error_message=UNCLAIMED_MESSAGE, finished_at=now)
    )
    db.commit()
    return result.rowcount == 1
