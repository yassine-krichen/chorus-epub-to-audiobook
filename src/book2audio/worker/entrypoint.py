"""Cloud Run Job container entrypoint: `python -m book2audio.worker.entrypoint`.

Downloads the epub (and any prior resume state, for retried executions),
runs the pipeline with progress callbacks wired to Postgres/R2, and reports
the terminal status back to the jobs table.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

from rich.console import Console

from book2audio.db.models import Job
from book2audio.db.session import SessionLocal
from book2audio.worker.render import (  # noqa: F401 - re-exported for tests/callers
    _build_config,
    _download_inputs,
    _set_total_chapters,
    render_job,
)

# R2 keys for one job's artifacts all live under this prefix, so a resumed
# execution can pull down everything it previously produced.
_SCRATCH_DIR = "/tmp/book2audio-job"


def main() -> int:
    job_id = uuid.UUID(_required_env("JOB_ID"))
    epub_object_key = _required_env("EPUB_OBJECT_KEY")
    job_config = json.loads(os.environ.get("JOB_CONFIG", "{}"))

    console = Console()
    scratch_dir = _SCRATCH_DIR
    os.makedirs(scratch_dir, exist_ok=True)

    try:
        render_job(job_id, epub_object_key, job_config, console, scratch_dir)
    except Exception as exc:  # noqa: BLE001 - top-level worker boundary
        _mark_failed(job_id, str(exc))
        console.print(f"[bold red]Worker failed:[/bold red] {exc}")
        return 1

    _mark_succeeded(job_id)
    return 0


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is not set")
    return value


def _mark_succeeded(job_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


def _mark_failed(job_id: uuid.UUID, message: str) -> None:
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = "failed"
        job.error_message = message
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


if __name__ == "__main__":
    sys.exit(main())
