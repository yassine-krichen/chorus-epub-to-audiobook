"""Tests for the hybrid-mode local worker daemon (book2audio.worker.local_daemon).

render_job is mocked throughout -- this module is only responsible for
claiming jobs, driving the terminal-status transitions, and not clobbering a
status the API already set. Real end-to-end rendering is
test_worker_render.py's and the pipeline tests' job.

Real Postgres SKIP LOCKED concurrency cannot be exercised here (the only DB
available in this environment is SQLite, and _claim_next_job() skips the
with_for_update(skip_locked=True) clause entirely on non-Postgres dialects) --
that's out of scope for this suite; only the SQLite fallback path is covered.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from book2audio.db.models import Job, User
from book2audio.worker import local_daemon as local_daemon_module
from book2audio.worker.local_daemon import JobCanceled, _ClaimedJob


def _make_user(db_session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_job(db_session, **overrides) -> Job:
    user = _make_user(db_session)
    defaults = dict(
        user_id=user.id,
        title="Book",
        epub_object_key="uploads/book.epub",
        config={},
        status="queued",
        chapters=[],
        parts=[],
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _claimed(job_id, epub_object_key="uploads/book.epub", config=None) -> _ClaimedJob:
    return _ClaimedJob(id=job_id, epub_object_key=epub_object_key, config=config or {})


# --- _claim_next_job ---------------------------------------------------


def test_claim_next_job_claims_the_oldest_queued_job_and_marks_it_running(db_session):
    older = _make_job(
        db_session, title="Older", created_at=datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    _make_job(db_session, title="Newer", created_at=datetime.now(timezone.utc))

    claimed = local_daemon_module._claim_next_job(db_session)

    assert claimed.id == older.id
    assert claimed.epub_object_key == older.epub_object_key
    assert claimed.config == older.config
    db_session.refresh(older)
    assert older.status == "running"
    assert older.started_at is not None
    assert older.last_heartbeat_at is not None


def test_claim_next_job_returns_none_when_no_job_is_queued(db_session):
    _make_job(db_session, status="running")

    assert local_daemon_module._claim_next_job(db_session) is None


def test_claim_next_job_never_reclaims_a_job_that_is_already_running(db_session):
    running = _make_job(db_session, status="running")

    claimed = local_daemon_module._claim_next_job(db_session)

    assert claimed is None
    db_session.refresh(running)
    assert running.status == "running"


def test_claim_next_job_never_claims_a_terminal_job(db_session):
    _make_job(db_session, status="succeeded")
    _make_job(db_session, status="failed")
    _make_job(db_session, status="canceled")

    assert local_daemon_module._claim_next_job(db_session) is None


# --- _raise_if_cancel_requested ------------------------------------------


def test_raise_if_cancel_requested_raises_job_canceled_when_the_flag_is_set(db_session):
    job = _make_job(db_session, status="running", cancel_requested_at=datetime.now(timezone.utc))

    with pytest.raises(JobCanceled):
        local_daemon_module._raise_if_cancel_requested(job.id)


def test_raise_if_cancel_requested_does_nothing_when_the_flag_is_unset(db_session):
    job = _make_job(db_session, status="running")

    local_daemon_module._raise_if_cancel_requested(job.id)  # must not raise


def test_raise_if_cancel_requested_does_nothing_when_the_job_row_no_longer_exists():
    local_daemon_module._raise_if_cancel_requested(uuid.uuid4())  # must not raise


# --- _mark_succeeded / _mark_failed: must never clobber a canceled status --


def test_mark_succeeded_marks_a_running_job_succeeded(db_session):
    job = _make_job(db_session, status="running")

    local_daemon_module._mark_succeeded(job.id)

    db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.finished_at is not None


def test_mark_failed_marks_a_running_job_failed_with_the_error_message(db_session):
    job = _make_job(db_session, status="running")

    local_daemon_module._mark_failed(job.id, "render blew up")

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == "render blew up"


def test_mark_succeeded_leaves_an_already_canceled_job_canceled(db_session):
    job = _make_job(db_session, status="canceled", finished_at=datetime.now(timezone.utc))

    local_daemon_module._mark_succeeded(job.id)

    db_session.refresh(job)
    assert job.status == "canceled"


def test_mark_failed_leaves_an_already_canceled_job_canceled_and_does_not_set_an_error_message(
    db_session,
):
    job = _make_job(db_session, status="canceled", finished_at=datetime.now(timezone.utc))

    local_daemon_module._mark_failed(job.id, "boom")

    db_session.refresh(job)
    assert job.status == "canceled"
    assert job.error_message is None


# --- _process_job ----------------------------------------------------------


def test_process_job_marks_the_job_succeeded_on_a_clean_render(db_session, monkeypatch):
    job = _make_job(db_session, status="running")
    monkeypatch.setattr(local_daemon_module, "render_job", MagicMock())

    local_daemon_module._process_job(_claimed(job.id))

    db_session.refresh(job)
    assert job.status == "succeeded"


def test_process_job_creates_and_removes_the_scratch_dir_on_success(db_session, monkeypatch):
    job = _make_job(db_session, status="running")
    captured = {}

    def _fake_render_job(job_id, epub_object_key, config, console, scratch_dir, **kwargs):
        captured["path"] = scratch_dir
        assert os.path.isdir(scratch_dir)

    monkeypatch.setattr(local_daemon_module, "render_job", _fake_render_job)

    local_daemon_module._process_job(_claimed(job.id))

    assert not os.path.exists(captured["path"])


def test_process_job_marks_the_job_failed_and_cleans_up_when_rendering_raises(
    db_session, monkeypatch
):
    job = _make_job(db_session, status="running")
    captured = {}

    def _fake_render_job(job_id, epub_object_key, config, console, scratch_dir, **kwargs):
        captured["path"] = scratch_dir
        raise RuntimeError("render blew up")

    monkeypatch.setattr(local_daemon_module, "render_job", _fake_render_job)

    local_daemon_module._process_job(_claimed(job.id))  # must not raise -- one bad job can't crash the daemon

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == "render blew up"
    assert not os.path.exists(captured["path"])


def test_process_job_wires_after_chapter_to_a_cancel_check_callback(db_session, monkeypatch):
    job = _make_job(db_session, status="running")
    captured = {}

    def _fake_render_job(job_id, epub_object_key, config, console, scratch_dir, *, after_chapter=None):
        captured["after_chapter"] = after_chapter

    monkeypatch.setattr(local_daemon_module, "render_job", _fake_render_job)

    local_daemon_module._process_job(_claimed(job.id))

    assert captured["after_chapter"] is not None


def test_process_job_still_cleans_up_the_scratch_dir_when_job_canceled_is_raised(
    db_session, monkeypatch
):
    job = _make_job(db_session, status="canceled")
    captured = {}

    def _fake_render_job(job_id, epub_object_key, config, console, scratch_dir, **kwargs):
        captured["path"] = scratch_dir
        raise JobCanceled(str(job_id))

    monkeypatch.setattr(local_daemon_module, "render_job", _fake_render_job)

    local_daemon_module._process_job(_claimed(job.id))

    assert not os.path.exists(captured["path"])


def test_process_job_does_not_clobber_a_status_the_api_already_set_to_canceled_when_render_raises_job_canceled(
    db_session, monkeypatch
):
    """A JobCanceled raised mid-render means the API already flipped the
    job's status to "canceled" (via cancel_job()) before the daemon caught
    up -- _process_job must not call _mark_succeeded/_mark_failed and
    overwrite that status back to something else."""
    job = _make_job(db_session, status="canceled", cancel_requested_at=datetime.now(timezone.utc))
    mark_succeeded = MagicMock()
    mark_failed = MagicMock()
    monkeypatch.setattr(local_daemon_module, "_mark_succeeded", mark_succeeded)
    monkeypatch.setattr(local_daemon_module, "_mark_failed", mark_failed)
    monkeypatch.setattr(
        local_daemon_module, "render_job", MagicMock(side_effect=JobCanceled(str(job.id)))
    )

    local_daemon_module._process_job(_claimed(job.id))

    mark_succeeded.assert_not_called()
    mark_failed.assert_not_called()
    db_session.refresh(job)
    assert job.status == "canceled"
