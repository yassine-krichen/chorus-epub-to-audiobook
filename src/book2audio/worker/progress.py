"""Progress-reporting closures wired into Pipeline's optional callbacks.

Each closure opens and closes its own DB session rather than sharing one for
the whole multi-hour render — Neon closes idle connections, and a render can
run for hours between chapters on a slow batch.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Callable

from book2audio.db.models import Job
from book2audio.db.session import SessionLocal
from book2audio.models import Chapter
from book2audio.storage.r2 import upload_file


def build_progress_hooks(
    job_id: uuid.UUID, work_dir: str, epub_object_prefix: str
) -> tuple[Callable[[Chapter], None], Callable[[int, str, list[Chapter]], None]]:
    def on_chapter_done(chapter: Chapter) -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.chapters = _upsert_chapter(job.chapters, chapter)
            job.last_heartbeat_at = datetime.now(timezone.utc)
            session.commit()

    def on_part_ready(part_no: int, out_path: str, batch: list[Chapter]) -> None:
        r2_key = f"{epub_object_prefix}/part-{part_no:02d}.m4b"
        upload_file(out_path, r2_key)
        _upload_state_json(work_dir, epub_object_prefix)

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.parts = _upsert_part(job.parts, part_no, batch, r2_key)
            job.last_heartbeat_at = datetime.now(timezone.utc)
            session.commit()

    return on_chapter_done, on_part_ready


def _upsert_chapter(chapters: list[dict], chapter: Chapter) -> list[dict]:
    entry = {"index": chapter.index, "title": chapter.title, "status": "done"}
    updated = [c for c in chapters if c.get("index") != chapter.index]
    updated.append(entry)
    updated.sort(key=lambda c: c["index"])
    return updated


def _upsert_part(
    parts: list[dict], part_no: int, batch: list[Chapter], r2_key: str
) -> list[dict]:
    entry = {
        "part_number": part_no,
        "first_index": batch[0].index,
        "last_index": batch[-1].index,
        "r2_key": r2_key,
        "status": "done",
    }
    updated = [p for p in parts if p.get("part_number") != part_no]
    updated.append(entry)
    updated.sort(key=lambda p: p["part_number"])
    return updated


def _upload_state_json(work_dir: str, epub_object_prefix: str) -> None:
    state_path = os.path.join(work_dir, "state.json")
    if os.path.exists(state_path):
        upload_file(state_path, f"{epub_object_prefix}/state.json")
