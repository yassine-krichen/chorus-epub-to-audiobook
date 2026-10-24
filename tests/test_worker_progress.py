"""Tests for the worker's progress-reporting hooks.

Uses the real (SQLite-backed) SessionLocal that book2audio.db.session exposes,
since build_progress_hooks' closures call SessionLocal() directly rather than
accepting an injected session. R2 upload is mocked — no real network call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from book2audio.db.models import Job, User
from book2audio.images.thumbnails import chapter_content_hash
from book2audio.models import Chapter, Segment, SegmentKind
from book2audio.worker import progress as progress_module
from book2audio.worker.progress import build_progress_hooks


def _make_job(db_session, **overrides) -> Job:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    defaults = dict(
        user_id=user.id,
        title="Book",
        epub_object_key="uploads/book.epub",
        config={},
        status="running",
        chapters=[],
        parts=[],
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _chapter(index=1, title="Chapter One") -> Chapter:
    return Chapter(index=index, title=title, segments=[Segment(kind=SegmentKind.NARRATION, text="Hi")])


def test_on_chapter_done_records_the_chapter_as_done_and_bumps_the_heartbeat(db_session):
    job = _make_job(db_session)
    on_chapter_done, _ = build_progress_hooks(job.id, "/tmp/work", "jobs/1")

    on_chapter_done(_chapter(index=1, title="Chapter One"))

    db_session.refresh(job)
    assert job.chapters == [{"index": 1, "title": "Chapter One", "status": "done"}]
    assert job.last_heartbeat_at is not None


def test_on_chapter_done_upserts_by_index_and_keeps_the_list_sorted(db_session):
    job = _make_job(db_session, chapters=[{"index": 2, "title": "Two", "status": "done"}])
    on_chapter_done, _ = build_progress_hooks(job.id, "/tmp/work", "jobs/1")

    on_chapter_done(_chapter(index=1, title="One"))

    db_session.refresh(job)
    assert [c["index"] for c in job.chapters] == [1, 2]


def test_on_chapter_done_replaces_a_re_rendered_chapters_entry_instead_of_duplicating(db_session):
    job = _make_job(db_session, chapters=[{"index": 1, "title": "Old Title", "status": "done"}])
    on_chapter_done, _ = build_progress_hooks(job.id, "/tmp/work", "jobs/1")

    on_chapter_done(_chapter(index=1, title="New Title"))

    db_session.refresh(job)
    assert job.chapters == [{"index": 1, "title": "New Title", "status": "done"}]


def test_on_chapter_done_is_a_no_op_when_the_job_row_no_longer_exists(db_session):
    on_chapter_done, _ = build_progress_hooks(uuid.uuid4(), "/tmp/work", "jobs/missing")
    on_chapter_done(_chapter())  # must not raise


def test_on_part_ready_uploads_the_part_and_records_it_on_the_job(db_session, monkeypatch, tmp_path):
    upload_file = MagicMock()
    monkeypatch.setattr(progress_module, "upload_file", upload_file)
    job = _make_job(db_session)
    on_chapter_done, on_part_ready = build_progress_hooks(job.id, str(tmp_path), "jobs/1")
    batch = [_chapter(index=1, title="One"), _chapter(index=2, title="Two")]

    on_part_ready(1, "/scratch/part-01.m4b", batch)

    upload_file.assert_any_call("/scratch/part-01.m4b", "jobs/1/part-01.m4b")
    db_session.refresh(job)
    assert job.parts == [
        {
            "part_number": 1,
            "first_index": 1,
            "last_index": 2,
            "r2_key": "jobs/1/part-01.m4b",
            "status": "done",
        }
    ]


def test_on_part_ready_also_uploads_state_json_when_it_exists_in_the_work_dir(
    db_session, monkeypatch, tmp_path
):
    upload_file = MagicMock()
    monkeypatch.setattr(progress_module, "upload_file", upload_file)
    (tmp_path / "state.json").write_text("{}")
    job = _make_job(db_session)
    _, on_part_ready = build_progress_hooks(job.id, str(tmp_path), "jobs/1")

    on_part_ready(1, "/scratch/part-01.m4b", [_chapter()])

    upload_file.assert_any_call(str(tmp_path / "state.json"), "jobs/1/state.json")


def test_on_part_ready_skips_state_json_upload_when_it_does_not_exist(
    db_session, monkeypatch, tmp_path
):
    upload_file = MagicMock()
    monkeypatch.setattr(progress_module, "upload_file", upload_file)
    job = _make_job(db_session)
    _, on_part_ready = build_progress_hooks(job.id, str(tmp_path), "jobs/1")

    on_part_ready(1, "/scratch/part-01.m4b", [_chapter()])

    uploaded_keys = [call.args[1] for call in upload_file.call_args_list]
    assert "jobs/1/state.json" not in uploaded_keys


def test_each_hook_invocation_opens_and_closes_its_own_session(db_session, monkeypatch, tmp_path):
    """Regression guard: a shared session held across a multi-hour render would
    exhaust Neon's connection limit. Each callback must use a fresh session
    that's closed again before returning."""
    monkeypatch.setattr(progress_module, "upload_file", MagicMock())
    job = _make_job(db_session)
    on_chapter_done, on_part_ready = build_progress_hooks(job.id, str(tmp_path), "jobs/1")

    created_sessions = []
    real_session_local = progress_module.SessionLocal

    def _tracking_session_local():
        session = real_session_local()
        created_sessions.append(session)
        return session

    monkeypatch.setattr(progress_module, "SessionLocal", _tracking_session_local)

    on_chapter_done(_chapter(index=1))
    on_chapter_done(_chapter(index=2))
    on_part_ready(1, "/scratch/part-01.m4b", [_chapter(index=1), _chapter(index=2)])

    assert len(created_sessions) == 3
    assert len(set(id(s) for s in created_sessions)) == 3
    for session in created_sessions:
        assert session.in_transaction() is False


# --- thumbnails ---------------------------------------------------------
#
# Backward compatibility (thumbnails_enabled defaults to False) is a hard
# requirement: the chapter dict shape written to the DB must be byte-identical
# to before this feature existed -- exact same keys, no thumbnail_* keys at
# all. build_progress_hooks() with no thumbnails kwargs (exercised by every
# test above this section) already proves that; the test below pins it down
# explicitly and additionally asserts the exact key set.


def test_on_chapter_done_with_thumbnails_disabled_by_default_omits_all_thumbnail_keys(db_session):
    job = _make_job(db_session)
    on_chapter_done, _ = build_progress_hooks(job.id, "/tmp/work", "jobs/1")

    on_chapter_done(_chapter(index=1, title="One"))

    db_session.refresh(job)
    assert job.chapters == [{"index": 1, "title": "One", "status": "done"}]
    assert set(job.chapters[0].keys()) == {"index", "title", "status"}


def test_on_chapter_done_with_thumbnails_enabled_generates_and_uploads_a_new_thumbnail(
    db_session, monkeypatch
):
    upload_bytes = MagicMock()
    monkeypatch.setattr(progress_module, "upload_bytes", upload_bytes)
    monkeypatch.setattr(
        progress_module, "try_generate_thumbnail", MagicMock(return_value=b"png-bytes")
    )
    job = _make_job(db_session)
    on_chapter_done, _ = build_progress_hooks(
        job.id, "/tmp/work", "jobs/1", thumbnails_enabled=True, book_title="My Book"
    )

    on_chapter_done(_chapter(index=1, title="One"))

    db_session.refresh(job)
    entry = job.chapters[0]
    assert entry["thumbnail_status"] == "ready"
    assert entry["thumbnail_r2_key"] == "jobs/1/thumbnails/chapter-0001.png"
    assert entry["thumbnail_hash"]
    upload_bytes.assert_called_once_with(
        b"png-bytes", "jobs/1/thumbnails/chapter-0001.png", content_type="image/png"
    )


def test_on_chapter_done_reuses_the_cached_thumbnail_when_content_hash_is_unchanged(
    db_session, monkeypatch
):
    chapter = _chapter(index=1, title="One")
    stable_hash = chapter_content_hash(chapter)
    job = _make_job(
        db_session,
        chapters=[
            {
                "index": 1,
                "title": "One",
                "status": "done",
                "thumbnail_r2_key": "jobs/1/thumbnails/chapter-0001.png",
                "thumbnail_hash": stable_hash,
                "thumbnail_status": "ready",
            }
        ],
    )
    generate_thumbnail = MagicMock()
    upload_bytes = MagicMock()
    monkeypatch.setattr(progress_module, "try_generate_thumbnail", generate_thumbnail)
    monkeypatch.setattr(progress_module, "upload_bytes", upload_bytes)
    on_chapter_done, _ = build_progress_hooks(
        job.id, "/tmp/work", "jobs/1", thumbnails_enabled=True, book_title="Book"
    )

    on_chapter_done(chapter)

    db_session.refresh(job)
    entry = job.chapters[0]
    assert entry["thumbnail_r2_key"] == "jobs/1/thumbnails/chapter-0001.png"
    assert entry["thumbnail_status"] == "ready"
    generate_thumbnail.assert_not_called()
    upload_bytes.assert_not_called()


def test_on_chapter_done_regenerates_the_thumbnail_when_content_hash_changed(
    db_session, monkeypatch
):
    job = _make_job(
        db_session,
        chapters=[
            {
                "index": 1,
                "title": "Old",
                "status": "done",
                "thumbnail_r2_key": "jobs/1/thumbnails/chapter-0001.png",
                "thumbnail_hash": "stale-hash",
                "thumbnail_status": "ready",
            }
        ],
    )
    generate_thumbnail = MagicMock(return_value=b"new-bytes")
    upload_bytes = MagicMock()
    monkeypatch.setattr(progress_module, "try_generate_thumbnail", generate_thumbnail)
    monkeypatch.setattr(progress_module, "upload_bytes", upload_bytes)
    on_chapter_done, _ = build_progress_hooks(
        job.id, "/tmp/work", "jobs/1", thumbnails_enabled=True, book_title="Book"
    )

    on_chapter_done(_chapter(index=1, title="New Title"))

    generate_thumbnail.assert_called_once()
    db_session.refresh(job)
    entry = job.chapters[0]
    assert entry["thumbnail_status"] == "ready"
    assert entry["thumbnail_hash"] != "stale-hash"


def test_on_chapter_done_records_failed_status_and_does_not_raise_when_generation_returns_none(
    db_session, monkeypatch
):
    monkeypatch.setattr(progress_module, "try_generate_thumbnail", MagicMock(return_value=None))
    upload_bytes = MagicMock()
    monkeypatch.setattr(progress_module, "upload_bytes", upload_bytes)
    job = _make_job(db_session)
    on_chapter_done, _ = build_progress_hooks(
        job.id, "/tmp/work", "jobs/1", thumbnails_enabled=True, book_title="Book"
    )

    on_chapter_done(_chapter(index=1))  # must not raise

    db_session.refresh(job)
    entry = job.chapters[0]
    assert entry["thumbnail_status"] == "failed"
    assert "thumbnail_r2_key" not in entry
    upload_bytes.assert_not_called()


def test_on_chapter_done_records_failed_status_and_does_not_raise_when_generation_raises(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        progress_module, "try_generate_thumbnail", MagicMock(side_effect=RuntimeError("boom"))
    )
    job = _make_job(db_session)
    on_chapter_done, _ = build_progress_hooks(
        job.id, "/tmp/work", "jobs/1", thumbnails_enabled=True, book_title="Book"
    )

    on_chapter_done(_chapter(index=1))  # must not raise

    db_session.refresh(job)
    assert job.chapters[0]["thumbnail_status"] == "failed"


# --- build_heartbeat ----------------------------------------------------------


def test_heartbeat_bumps_last_heartbeat_at(db_session):
    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    job = _make_job(db_session, last_heartbeat_at=stale)

    progress_module.build_heartbeat(job.id)()

    db_session.refresh(job)
    heartbeat = job.last_heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - heartbeat < timedelta(minutes=1)


def test_heartbeat_does_not_raise_when_the_job_row_no_longer_exists():
    progress_module.build_heartbeat(uuid.uuid4())()  # must not raise


def test_heartbeat_swallows_database_errors_so_a_blip_cannot_abort_the_render(monkeypatch):
    def broken_session():
        raise RuntimeError("connection reset")

    monkeypatch.setattr(progress_module, "SessionLocal", broken_session)

    progress_module.build_heartbeat(uuid.uuid4())()  # must not raise
