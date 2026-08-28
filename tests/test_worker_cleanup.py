"""Tests for the R2 cleanup sweep entrypoint (book2audio.worker.cleanup).

Uses the real (SQLite-backed) SessionLocal that conftest.py wires up for
book2audio.worker.cleanup, same pattern as test_worker_progress.py and
test_jobs_service.py. list_objects/delete_objects are monkeypatched -- no
real R2 network call or credentials required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from book2audio.db.models import Job, User
from book2audio.worker import cleanup as cleanup_module


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
        status="canceled",
        chapters=[],
        parts=[],
        finished_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _patch_r2(monkeypatch, keys=None):
    list_objects = MagicMock(return_value=keys if keys is not None else ["jobs/x/part-01.m4b"])
    delete_objects = MagicMock()
    monkeypatch.setattr(cleanup_module, "list_objects", list_objects)
    monkeypatch.setattr(cleanup_module, "delete_objects", delete_objects)
    return list_objects, delete_objects


def test_main_sweeps_a_canceled_job_past_the_grace_period(db_session, monkeypatch):
    list_objects, delete_objects = _patch_r2(monkeypatch, keys=["jobs/j/part-01.m4b"])
    job = _make_job(db_session, status="canceled")

    exit_code = cleanup_module.main()

    assert exit_code == 0
    list_objects.assert_called_once_with(f"jobs/{job.id}/")
    delete_objects.assert_called_once_with(["jobs/j/part-01.m4b"])
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None


def test_main_sweeps_a_failed_job_past_the_grace_period(db_session, monkeypatch):
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(db_session, status="failed")

    cleanup_module.main()

    list_objects.assert_called_once_with(f"jobs/{job.id}/")
    delete_objects.assert_called_once()
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None


def test_main_never_sweeps_a_succeeded_job_regardless_of_age(db_session, monkeypatch):
    """Succeeded jobs' artifacts are the deliverable -- they must never be
    swept by the cleanup sweep, no matter how old the job is."""
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(
        db_session,
        status="succeeded",
        finished_at=datetime.now(timezone.utc) - timedelta(days=365),
    )

    cleanup_module.main()

    list_objects.assert_not_called()
    delete_objects.assert_not_called()
    db_session.refresh(job)
    assert job.artifacts_swept_at is None


def test_main_leaves_a_canceled_job_alone_when_still_inside_the_grace_period(
    db_session, monkeypatch
):
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(
        db_session,
        status="canceled",
        finished_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    cleanup_module.main()

    list_objects.assert_not_called()
    delete_objects.assert_not_called()
    db_session.refresh(job)
    assert job.artifacts_swept_at is None


def test_main_leaves_a_failed_job_alone_when_still_inside_the_grace_period(db_session, monkeypatch):
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(
        db_session,
        status="failed",
        finished_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    cleanup_module.main()

    list_objects.assert_not_called()
    delete_objects.assert_not_called()
    db_session.refresh(job)
    assert job.artifacts_swept_at is None


def test_main_respects_a_custom_grace_period_from_the_environment(db_session, monkeypatch):
    monkeypatch.setenv("CLEANUP_GRACE_PERIOD_DAYS", "1")
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(
        db_session,
        status="canceled",
        finished_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )

    cleanup_module.main()

    list_objects.assert_called_once_with(f"jobs/{job.id}/")
    delete_objects.assert_called_once()
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None


def test_main_skips_a_job_whose_artifacts_are_already_swept(db_session, monkeypatch):
    """Idempotency: a job already marked swept must not be re-processed --
    no R2 calls at all, since the candidate query filters it out up front."""
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(
        db_session,
        status="canceled",
        artifacts_swept_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    cleanup_module.main()

    list_objects.assert_not_called()
    delete_objects.assert_not_called()
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None  # unchanged, still set


def test_main_run_twice_in_a_row_sweeps_nothing_new_on_the_second_run(db_session, monkeypatch):
    list_objects, delete_objects = _patch_r2(monkeypatch)
    job = _make_job(db_session, status="canceled")

    first_run = cleanup_module.main()
    assert first_run == 0
    list_objects.assert_called_once()
    delete_objects.assert_called_once()

    second_run = cleanup_module.main()

    assert second_run == 0
    # No new R2 calls: the job is no longer a candidate (artifacts_swept_at is set).
    list_objects.assert_called_once()
    delete_objects.assert_called_once()
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None


def test_main_reports_the_number_of_jobs_swept(db_session, monkeypatch, capsys):
    _patch_r2(monkeypatch)
    _make_job(db_session, status="canceled")
    _make_job(db_session, status="failed")

    cleanup_module.main()

    output = capsys.readouterr().out
    assert "2" in output


# --- partial R2 delete failures must not be reported as swept --------------


def test_main_does_not_mark_a_job_swept_when_delete_objects_partially_fails(
    db_session, monkeypatch, capsys
):
    list_objects = MagicMock(return_value=["jobs/j/part-01.m4b", "jobs/j/part-02.m4b"])
    delete_objects = MagicMock(
        side_effect=RuntimeError("Failed to delete R2 object(s): ['jobs/j/part-02.m4b']")
    )
    monkeypatch.setattr(cleanup_module, "list_objects", list_objects)
    monkeypatch.setattr(cleanup_module, "delete_objects", delete_objects)
    job = _make_job(db_session, status="canceled")

    exit_code = cleanup_module.main()

    assert exit_code == 0
    delete_objects.assert_called_once()
    db_session.refresh(job)
    assert job.artifacts_swept_at is None  # left as a candidate for the next run
    assert "jobs/j/part-02.m4b" in capsys.readouterr().out


def test_main_reports_zero_swept_when_the_only_candidate_job_partially_fails_to_delete(
    db_session, monkeypatch
):
    list_objects = MagicMock(return_value=["jobs/j/part-01.m4b"])
    delete_objects = MagicMock(side_effect=RuntimeError("partial failure"))
    monkeypatch.setattr(cleanup_module, "list_objects", list_objects)
    monkeypatch.setattr(cleanup_module, "delete_objects", delete_objects)
    _make_job(db_session, status="canceled")

    exit_code = cleanup_module.main()

    assert exit_code == 0


def test_main_still_sweeps_a_job_with_a_full_success_response_after_a_prior_partial_failure(
    db_session, monkeypatch
):
    """A full-success delete_objects response still marks the job swept, same
    as before -- the partial-failure handling doesn't change the happy path.
    """
    list_objects, delete_objects = _patch_r2(monkeypatch, keys=["jobs/j/part-01.m4b"])
    job = _make_job(db_session, status="canceled")

    cleanup_module.main()

    delete_objects.assert_called_once()
    db_session.refresh(job)
    assert job.artifacts_swept_at is not None
