"""Cloud Run Job entrypoint: `python -m book2audio.worker.cleanup`.

Sweeps R2 artifacts for jobs that finished terminally (canceled/failed) more
than a grace period ago, so storage doesn't grow unbounded for renders nobody
ever downloaded. Succeeded jobs are left alone — those artifacts are the
actual product and stay until the user deletes them some other way.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from book2audio.db.models import Job
from book2audio.db.session import SessionLocal
from book2audio.storage.r2 import delete_objects, list_objects

_DEFAULT_GRACE_PERIOD_DAYS = 7


def main() -> int:
    grace_period = timedelta(days=_grace_period_days())
    swept = 0

    with SessionLocal() as session:
        candidate_ids = session.scalars(
            select(Job.id).where(
                Job.status.in_(("canceled", "failed")),
                Job.artifacts_swept_at.is_(None),
            )
        ).all()

    for job_id in candidate_ids:
        if _sweep_job(job_id, grace_period):
            swept += 1

    print(f"Cleanup swept {swept} job(s)")
    return 0


def _grace_period_days() -> int:
    raw = os.environ.get("CLEANUP_GRACE_PERIOD_DAYS")
    return int(raw) if raw else _DEFAULT_GRACE_PERIOD_DAYS


def _sweep_job(job_id: uuid.UUID, grace_period: timedelta) -> bool:
    """Delete R2 artifacts for one job if it's past the grace period.

    Opens/closes its own short-lived session per job, same pattern as
    worker/progress.py's per-callback sessions -- a sweep can iterate many
    jobs and a long-held session isn't worth keeping open the whole time.
    """
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None or job.artifacts_swept_at is not None:
            return False
        if job.status not in ("canceled", "failed"):
            return False

        finished_at = job.finished_at
        if finished_at is None:
            return False
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - finished_at < grace_period:
            return False

        try:
            delete_objects(list_objects(f"jobs/{job_id}/"))
        except RuntimeError as error:
            # delete_objects raises on partial R2 failures (e.g. a key that
            # transiently failed to delete). Leave artifacts_swept_at unset
            # so this job stays a sweep candidate on the next run instead of
            # silently losing track of orphaned objects.
            print(f"Cleanup failed to sweep job {job_id}: {error}")
            return False

        job.artifacts_swept_at = datetime.now(timezone.utc)
        session.commit()
        return True


if __name__ == "__main__":
    sys.exit(main())
