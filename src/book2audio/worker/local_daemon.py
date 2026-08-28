"""Hybrid-mode local worker: `python -m book2audio.worker.local_daemon`.

Runs on the user's own machine and polls the jobs table for queued work
instead of being triggered by Cloud Run. Used together with local MinIO
(exposed via a Cloudflare Tunnel) in place of R2 -- see DEPLOYMENT.md for the
full hybrid-mode setup. The Neon/Render/Vercel pieces are unchanged; only the
render worker and storage backend move local.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from time import sleep

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from book2audio.db.models import Job
from book2audio.db.session import SessionLocal
from book2audio.worker.render import render_job

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_SECONDS = 5


class JobCanceled(Exception):
    """Raised to unwind a render once the API has recorded a cancel request."""


@dataclass(frozen=True)
class _ClaimedJob:
    id: uuid.UUID
    epub_object_key: str
    config: dict


def main() -> None:
    poll_interval = float(
        os.environ.get("LOCAL_WORKER_POLL_INTERVAL_SECONDS", _DEFAULT_POLL_INTERVAL_SECONDS)
    )
    print(
        f"book2audio local worker started, polling every {poll_interval:g}s... "
        "(Ctrl+C to stop)"
    )
    while True:
        with SessionLocal() as db:
            claimed = _claim_next_job(db)
        if claimed is None:
            sleep(poll_interval)
            continue
        _process_job(claimed)


def _claim_next_job(db: Session) -> _ClaimedJob | None:
    query = select(Job).where(Job.status == "queued").order_by(Job.created_at.asc()).limit(1)
    # SKIP LOCKED is what makes concurrent claiming safe against races -- but
    # it's Postgres-only, and the test suite runs against SQLite.
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job is None:
        return None

    now = datetime.now(timezone.utc)
    job.status = "running"
    job.started_at = now
    job.last_heartbeat_at = now
    db.commit()
    return _ClaimedJob(id=job.id, epub_object_key=job.epub_object_key, config=job.config)


def _process_job(claimed: _ClaimedJob) -> None:
    console = Console()
    scratch_dir = os.path.join(tempfile.gettempdir(), f"book2audio-job-{claimed.id}")
    os.makedirs(scratch_dir, exist_ok=True)
    try:
        render_job(
            claimed.id,
            claimed.epub_object_key,
            claimed.config,
            console,
            scratch_dir,
            after_chapter=lambda chapter: _raise_if_cancel_requested(claimed.id),
        )
    except JobCanceled:
        console.print(f"[yellow]Job {claimed.id} canceled, stopping.[/yellow]")
    except Exception as exc:  # noqa: BLE001 - one bad job must not crash the daemon
        logger.exception("Local worker job %s failed", claimed.id)
        _mark_failed(claimed.id, str(exc))
        console.print(f"[bold red]Worker failed:[/bold red] {exc}")
    else:
        _mark_succeeded(claimed.id)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _raise_if_cancel_requested(job_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is not None and job.cancel_requested_at is not None:
            raise JobCanceled(str(job_id))


def _mark_succeeded(job_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None or job.status == "canceled":
            return
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


def _mark_failed(job_id: uuid.UUID, message: str) -> None:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None or job.status == "canceled":
            return
        job.status = "failed"
        job.error_message = message
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


if __name__ == "__main__":
    main()
