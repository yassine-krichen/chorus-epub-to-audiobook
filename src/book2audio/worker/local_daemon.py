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
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from time import sleep

from rich.console import Console
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from book2audio.cast_review import AWAITING_CAST_REVIEW
from book2audio.db.models import ClonedVoice, Job
from book2audio.db.session import SessionLocal
from book2audio.db.worker_heartbeat import record_heartbeat
from book2audio.tts.clone_engine import CLONE_CAPABILITY, probe_clone_capability, prune_prompt_cache
from book2audio.worker.render import render_job

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_SECONDS = 5
_HEARTBEAT_INTERVAL_SECONDS = 20
# Cached voice prompts of deleted voices are removed this often (and at start).
_PROMPT_PRUNE_INTERVAL_SECONDS = 600


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
    capabilities = _detect_capabilities()
    print(
        f"book2audio local worker started, polling every {poll_interval:g}s... "
        f"capabilities: {', '.join(capabilities) or 'none'} (Ctrl+C to stop)"
    )
    can_clone = CLONE_CAPABILITY in capabilities
    threading.Thread(
        target=_heartbeat_loop, args=(_worker_id(), capabilities), daemon=True
    ).start()
    while True:
        with SessionLocal() as db:
            claimed = _claim_next_job(db, can_clone=can_clone)
        if claimed is None:
            sleep(poll_interval)
            continue
        _process_job(claimed)


def _detect_capabilities() -> list[str]:
    """What this machine can do beyond Kokoro: today, speak cloned voices."""
    capability = probe_clone_capability()
    if capability.available:
        print(f"Voice cloning is available on {capability.device or 'this GPU'}.")
        return [CLONE_CAPABILITY]
    print(f"Voice cloning is off: {capability.reason}")
    return []


def _worker_id() -> str:
    return (os.environ.get("LOCAL_WORKER_ID") or socket.gethostname())[:80]


def _heartbeat_loop(worker_id: str, capabilities: list[str]) -> None:
    """Tell the API this worker is alive and what it can do, for as long as it runs.

    A thread, not part of the poll loop: a render can keep the loop busy for hours, and the
    API must still see the worker as online (and its cloned voices as usable).
    """
    stop = threading.Event()
    last_prune = 0.0

    while not stop.is_set():
        try:
            with SessionLocal() as session:
                record_heartbeat(session, worker_id, capabilities)
            if time.monotonic() - last_prune >= _PROMPT_PRUNE_INTERVAL_SECONDS:
                _prune_cached_prompts()
                last_prune = time.monotonic()
        except Exception:  # noqa: BLE001 - a missed beat must never stop the next one
            logger.warning("worker.heartbeat_failed", exc_info=True)
        stop.wait(_HEARTBEAT_INTERVAL_SECONDS)


def _prune_cached_prompts() -> None:
    """Drop cached voice prompts whose voice was deleted, so no trace of a recording stays."""
    with SessionLocal() as session:
        live = set(session.scalars(select(ClonedVoice.ref_sha256)))
    prune_prompt_cache(live)


def _claim_next_job(db: Session, *, can_clone: bool = False) -> _ClaimedJob | None:
    query = select(Job).where(Job.status == "queued")
    if not can_clone:
        # A job that names a cloned voice waits for a worker that has the clone engine.
        query = query.where(Job.requires_clone.is_(False))
    query = query.order_by(Job.created_at.asc()).limit(1)
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
        outcome = render_job(
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
        if outcome == AWAITING_CAST_REVIEW:
            # Parked for the user: the status is already set. Confirming the cast queues
            # the job again, and the resumed render restores its state from storage.
            console.print(f"[yellow]Job {claimed.id} waits for its cast review.[/yellow]")
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
    # Conditional UPDATEs: only a job still "running" is finished by its worker, so a
    # cancel (or a job parked for review) is never overwritten by a stale write.
    with SessionLocal() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(status="succeeded", finished_at=datetime.now(timezone.utc))
        )
        session.commit()


def _mark_failed(job_id: uuid.UUID, message: str) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(("queued", "running")))
            .values(status="failed", error_message=message, finished_at=datetime.now(timezone.utc))
        )
        session.commit()


if __name__ == "__main__":
    main()
