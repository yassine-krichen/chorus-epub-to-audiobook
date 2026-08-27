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

from book2audio.config import Config
from book2audio.db.models import Job
from book2audio.db.session import SessionLocal
from book2audio.epub import parse_epub
from book2audio.images.freellmapi import require_freellmapi_configured
from book2audio.pipeline import Pipeline
from book2audio.storage.r2 import download_file
from book2audio.worker.progress import build_progress_hooks

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
    object_prefix = f"jobs/{job_id}"

    try:
        epub_path = _download_inputs(epub_object_key, object_prefix, scratch_dir)
        config = _build_config(epub_path, scratch_dir, job_config)
        if config.thumbnails:
            require_freellmapi_configured()
        book_title = _set_total_chapters(job_id, config)

        on_chapter_done, on_part_ready = build_progress_hooks(
            job_id,
            scratch_dir,
            object_prefix,
            thumbnails_enabled=config.thumbnails,
            book_title=book_title,
        )
        pipeline = Pipeline(
            config,
            console,
            on_chapter_done=on_chapter_done,
            on_part_ready=on_part_ready,
        )
        pipeline.run()
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


def _download_inputs(epub_object_key: str, object_prefix: str, scratch_dir: str) -> str:
    epub_path = os.path.join(scratch_dir, "book.epub")
    download_file(epub_object_key, epub_path)

    # Best-effort resume: a prior partial execution's state.json and chapter
    # wavs let RenderState skip already-rendered chapters.
    state_path = os.path.join(scratch_dir, "state.json")
    try:
        download_file(f"{object_prefix}/state.json", state_path)
    except Exception:
        pass
    return epub_path


def _build_config(epub_path: str, scratch_dir: str, job_config: dict) -> Config:
    output_path = os.path.join(scratch_dir, "book.m4b")
    kwargs = {
        k: v
        for k, v in job_config.items()
        if k in Config.__dataclass_fields__ and v is not None
    }
    return Config(epub_path=epub_path, output_path=output_path, work_dir=scratch_dir, **kwargs)


def _set_total_chapters(job_id: uuid.UUID, config: Config) -> str:
    """Record the book's total chapter count and return its title.

    Parses the EPUB once here rather than a second time in the pipeline's own
    parse, purely to size the progress bar and (when thumbnails are enabled)
    thread the title into the progress hooks.
    """
    metadata, parsed = parse_epub(config.epub_path, cover_dir=config.work_dir)
    total = len(parsed) if config.chapter_limit <= 0 else min(config.chapter_limit, len(parsed))
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return metadata.title
        job.total_chapters = total
        job.last_heartbeat_at = datetime.now(timezone.utc)
        session.commit()
    return metadata.title


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
