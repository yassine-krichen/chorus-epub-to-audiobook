"""Shared render orchestration for both worker entrypoints.

`worker.entrypoint` (Cloud Run Job) and `worker.local_daemon` (hybrid-mode
local worker) both need to download a job's inputs, build its Config, and
run the Pipeline with progress hooks wired to Postgres/R2 -- this module is
the one place that logic lives so the two entrypoints can't drift apart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Literal

from rich.console import Console
from rich.markup import escape
from sqlalchemy import select

from book2audio.attribution import require_llm_attribution_configured
from book2audio.cast_report import CAST_REPORT_FILENAME
from book2audio.cast_review import AWAITING_CAST_REVIEW
from book2audio.config import Config
from book2audio.api.voices.reference_audio import MAX_UPLOAD_BYTES
from book2audio.db.models import ClonedVoice, Job
from book2audio.db.session import SessionLocal
from book2audio.epub import parse_epub
from book2audio.images.enhanced import build_enhanced_thumbnails
from book2audio.images.freellmapi import require_freellmapi_configured
from book2audio.models import Chapter
from book2audio.pipeline import Pipeline
from book2audio.storage.r2 import download_capped, download_file
from book2audio.tts.clone_engine import (
    CloneReference,
    QwenCloneEngine,
    load_clone_engine,
    probe_clone_capability,
)
from book2audio.tts.voices import clone_voice_id, clone_voice_ids
from book2audio.worker.progress import (
    JOB_RESUME_FILENAMES,
    CastReviewRequested,
    build_cast_report_uploader,
    build_cast_review_pause,
    build_heartbeat,
    build_progress_hooks,
)

logger = logging.getLogger(__name__)

RenderOutcome = Literal["finished", "awaiting_cast_review"]


def render_job(
    job_id: uuid.UUID,
    epub_object_key: str,
    job_config: dict,
    console: Console,
    scratch_dir: str,
    *,
    after_chapter: Callable[[Chapter], None] | None = None,
) -> RenderOutcome:
    """Run one job to the end, or to the cast review pause.

    Returns ``AWAITING_CAST_REVIEW`` when the job was parked for the user (the caller
    must then leave its status alone); ``"finished"`` when the render completed.
    """
    object_prefix = f"jobs/{job_id}"

    epub_path = _download_inputs(epub_object_key, object_prefix, scratch_dir)
    config = _build_config(epub_path, scratch_dir, job_config)
    if config.thumbnails:
        require_freellmapi_configured()
    if config.attributor != "heuristic" and config.voice_mode == "cast":
        require_llm_attribution_configured()
    book_title = _set_total_chapters(job_id, config)
    enhanced = (
        build_enhanced_thumbnails(scratch_dir, book_title) if config.thumbnails_enhanced else None
    )

    on_chapter_done, on_part_ready = build_progress_hooks(
        job_id,
        scratch_dir,
        object_prefix,
        thumbnails_enabled=config.thumbnails,
        book_title=book_title,
        enhanced=enhanced,
    )
    if after_chapter is not None:
        _base_on_chapter_done = on_chapter_done

        def on_chapter_done(chapter: Chapter) -> None:  # noqa: F811 - intentional wrap
            _base_on_chapter_done(chapter)
            after_chapter(chapter)

    clone_voices = _load_clone_references(job_id, scratch_dir, config, console)
    pipeline = Pipeline(
        config,
        console,
        on_chapter_done=on_chapter_done,
        on_part_ready=on_part_ready,
        on_attribution_progress=build_heartbeat(job_id),
        on_cast_ready=enhanced.set_cast if enhanced is not None else None,
        on_cast_report=_cast_report_hook(job_id, config, scratch_dir, object_prefix),
        clone_voices=clone_voices,
        clone_engine_factory=_clone_engine_factory(console),
    )
    try:
        pipeline.run()
    except CastReviewRequested:
        return AWAITING_CAST_REVIEW
    if pipeline.clone_fallbacks:
        # The report was published before the render; cloning failed after that.
        build_cast_report_uploader(job_id)(os.path.join(scratch_dir, CAST_REPORT_FILENAME))
    return "finished"


def _clone_engine_factory(console: Console) -> Callable[[], QwenCloneEngine | None]:
    """Starts the clone engine only on a machine that really has it (GPU, weights, libraries)."""

    def factory() -> QwenCloneEngine | None:
        capability = probe_clone_capability()
        if not capability.available:
            console.print(f"[yellow]Voice cloning is not available here: {capability.reason}[/yellow]")
            return None
        return load_clone_engine()

    return factory


def _load_clone_references(
    job_id: uuid.UUID, scratch_dir: str, config: Config, console: Console
) -> dict[str, CloneReference]:
    """The reference clips of the cloned voices a job names, downloaded and verified.

    Ownership is checked again here, against the job's own user: whatever the API let
    through, a worker never reads a recording that does not belong to the job's owner. A voice
    that is gone, foreign or damaged is simply left out, so Kokoro reads it instead.
    """
    wanted = clone_voice_ids([config.narrator_voice, *config.voice_overrides.values()])
    if not wanted:
        return {}
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return {}
        rows = list(
            session.scalars(
                select(ClonedVoice).where(
                    ClonedVoice.user_id == job.user_id, ClonedVoice.id.in_(wanted)
                )
            )
        )
    references: dict[str, CloneReference] = {}
    refs_dir = os.path.join(scratch_dir, "clone-refs")
    os.makedirs(refs_dir, exist_ok=True)
    for row in rows:
        path = os.path.join(refs_dir, f"{row.id}.wav")
        if not row.ref_object_key.startswith(f"voice-refs/{row.user_id}/"):
            continue  # never read a key outside the owner's own prefix
        try:
            digest = None
            if download_capped(row.ref_object_key, path, max_bytes=MAX_UPLOAD_BYTES):
                with open(path, "rb") as clip:
                    digest = hashlib.sha256(clip.read()).hexdigest()
        except (OSError, ValueError, RuntimeError):
            logger.warning("clone.reference_unreadable", extra={"voice_id": str(row.id)}, exc_info=True)
            digest = None
        if digest != row.ref_sha256:
            console.print(
                f"[yellow]The recording for cloned voice {escape(repr(row.name))} is unusable.[/yellow]"
            )
            continue
        references[clone_voice_id(row.id)] = CloneReference(
            voice_id=clone_voice_id(row.id),
            wav_path=path,
            ref_text=row.ref_text,
            sha256=row.ref_sha256,
            gender="f" if row.gender == "f" else "m",
        )
    return references


def _cast_report_hook(
    job_id: uuid.UUID, config: Config, scratch_dir: str, object_prefix: str
) -> Callable[[str], None]:
    """What to do with the cast report once it exists: publish it, or park the job on it.

    A job with ``cast_review`` on pauses when it has at least one character to review;
    with nobody to review (or narrator-only) it just carries on.
    """
    publish = build_cast_report_uploader(job_id)
    if not (config.cast_review and config.voice_mode == "cast"):
        return publish
    pause_for_review = build_cast_review_pause(job_id, scratch_dir, object_prefix)

    def on_cast_report(report_path: str) -> None:
        if _report_has_characters(report_path):
            pause_for_review(report_path)
        else:
            publish(report_path)

    return on_cast_report


def _report_has_characters(report_path: str) -> bool:
    try:
        with open(report_path, encoding="utf-8") as report_file:
            return bool(json.load(report_file).get("characters"))
    except (OSError, ValueError):
        return False  # an unreadable report is nothing to review: render rather than stall


def _download_inputs(epub_object_key: str, object_prefix: str, scratch_dir: str) -> str:
    epub_path = os.path.join(scratch_dir, "book.epub")
    download_file(epub_object_key, epub_path)

    # Best-effort resume: a prior partial execution's state.json and chapter
    # wavs let RenderState skip already-rendered chapters.
    for filename in JOB_RESUME_FILENAMES:
        try:
            download_file(f"{object_prefix}/{filename}", os.path.join(scratch_dir, filename))
        except Exception:
            pass
    return epub_path


def _build_config(epub_path: str, scratch_dir: str, job_config: dict) -> Config:
    output_path = os.path.join(scratch_dir, "book.m4b")
    # filename_template only names hosted downloads (the API applies it). The
    # worker keeps the legacy scratch names, so chapter titles from the EPUB
    # never reach its file names, and a template stored by an older version
    # cannot fail Config validation and block a resumed render.
    kwargs = {
        k: v
        for k, v in job_config.items()
        if k in Config.__dataclass_fields__
        and k not in {"filename_template", "legacy_file_names"}
        and v is not None
    }
    return Config(
        epub_path=epub_path,
        output_path=output_path,
        work_dir=scratch_dir,
        legacy_file_names=True,
        **kwargs,
    )


def _set_total_chapters(job_id: uuid.UUID, config: Config) -> str:
    """Record the book's total chapter count and return its title.

    Parses the EPUB once here rather than a second time in the pipeline's own
    parse, purely to size the progress bar and (when thumbnails are enabled)
    thread the title into the progress hooks.
    """
    metadata, parsed = parse_epub(config.epub_path, cover_dir=config.work_dir)
    start_pos, end_pos = config.selection_bounds(parsed)
    total = max(0, end_pos - start_pos)
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return metadata.title
        job.total_chapters = total
        job.last_heartbeat_at = datetime.now(timezone.utc)
        session.commit()
    return metadata.title
