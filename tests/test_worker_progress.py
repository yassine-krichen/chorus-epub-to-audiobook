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
