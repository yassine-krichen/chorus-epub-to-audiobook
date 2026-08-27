"""Unit tests for book2audio.api.jobs.service against a SQLite-backed session.

Cloud Run is never called for real: run_job_execution/get_execution are
monkeypatched so these tests exercise only book2audio's own logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from book2audio.api.jobs import service
from book2audio.api.jobs.schemas import CreateJobRequest, JobConfig
from book2audio.db.models import Job, User


def _make_user(db_session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_job(db_session, user_id, **overrides) -> Job:
    defaults = dict(
        user_id=user_id,
        title="My Book",
        epub_object_key="uploads/x.epub",
        config={},
        status="running",
        chapters=[],
        parts=[],
        cloud_run_execution_name="projects/p/locations/l/jobs/j/executions/e1",
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_create_job_starts_running_with_a_cloud_run_execution_recorded(db_session, monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e9"}}),
    )
    user = _make_user(db_session)
    payload = CreateJobRequest(epub_object_key="uploads/book.epub", title="A Book")

    job = service.create_job(db_session, user.id, payload)

    assert job.status == "running"
    assert job.cloud_run_execution_name == "projects/p/locations/l/jobs/j/executions/e9"
    assert job.started_at is not None
    assert job.last_heartbeat_at == job.started_at


def test_create_job_persists_the_submitted_config_as_the_jsonb_config(db_session, monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "exec-1"}}),
    )
    user = _make_user(db_session)
    payload = CreateJobRequest(
        epub_object_key="uploads/book.epub",
        title="A Book",
        config=JobConfig(narrator_voice="am_michael", speed=1.25),
    )

    job = service.create_job(db_session, user.id, payload)

    assert job.config["narrator_voice"] == "am_michael"
    assert job.config["speed"] == 1.25


def test_create_job_marks_the_job_failed_when_cloud_run_trigger_raises(db_session, monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(side_effect=httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock()
        )),
    )
    user = _make_user(db_session)
    payload = CreateJobRequest(epub_object_key="uploads/book.epub", title="A Book")

    with pytest.raises(HTTPException) as exc_info:
        service.create_job(db_session, user.id, payload)

    assert exc_info.value.status_code == 502

    job = db_session.scalar(service.select(Job).where(Job.user_id == user.id))
    assert job.status == "failed"
    assert job.error_message == "failed to start render job"
    assert job.cloud_run_execution_name is None


def test_get_job_with_a_fresh_heartbeat_does_not_call_cloud_run(db_session, monkeypatch):
    get_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "get_execution", get_execution)
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "running"
    get_execution.assert_not_called()


def test_get_job_with_a_stale_heartbeat_and_successful_execution_marks_job_succeeded(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        service.cloud_run,
        "get_execution",
        MagicMock(return_value={"completionTime": "2026-01-01T00:00:00Z", "succeededCount": 1, "failedCount": 0}),
    )
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "succeeded"
    assert result.finished_at is not None


def test_get_job_with_a_stale_heartbeat_and_failed_execution_marks_job_failed_with_a_message(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        service.cloud_run,
        "get_execution",
        MagicMock(return_value={"completionTime": "2026-01-01T00:00:00Z", "succeededCount": 0, "failedCount": 1}),
    )
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "failed"
    assert result.error_message


def test_get_job_with_a_stale_heartbeat_but_still_running_execution_leaves_job_running(
    db_session, monkeypatch
):
    get_execution = MagicMock(return_value={"succeededCount": 0, "failedCount": 0})
    monkeypatch.setattr(service.cloud_run, "get_execution", get_execution)
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "running"
    get_execution.assert_called_once()


def test_get_job_returns_none_when_the_job_belongs_to_a_different_user(db_session, monkeypatch):
    monkeypatch.setattr(service.cloud_run, "get_execution", MagicMock())
    owner = _make_user(db_session)
    other = _make_user(db_session)
    job = _make_job(db_session, owner.id)

    assert service.get_job(db_session, other.id, job.id) is None


def test_get_job_returns_none_for_a_nonexistent_job_id(db_session):
    user = _make_user(db_session)
    assert service.get_job(db_session, user.id, uuid.uuid4()) is None


def test_cancel_job_marks_a_running_job_canceled_and_calls_cloud_run(db_session, monkeypatch):
    cancel_execution = MagicMock(return_value={})
    monkeypatch.setattr(service.cloud_run, "cancel_execution", cancel_execution)
    user = _make_user(db_session)
    job = _make_job(db_session, user.id, status="running")

    result = service.cancel_job(db_session, user.id, job.id)

    assert result.status == "canceled"
    assert result.finished_at is not None
    cancel_execution.assert_called_once_with(job.cloud_run_execution_name)


def test_cancel_job_leaves_an_already_terminal_job_unchanged(db_session, monkeypatch):
    cancel_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "cancel_execution", cancel_execution)
    user = _make_user(db_session)
    job = _make_job(db_session, user.id, status="succeeded", finished_at=datetime.now(timezone.utc))

    result = service.cancel_job(db_session, user.id, job.id)

    assert result.status == "succeeded"
    cancel_execution.assert_not_called()


def test_cancel_job_returns_none_when_the_job_belongs_to_a_different_user(db_session):
    owner = _make_user(db_session)
    other = _make_user(db_session)
    job = _make_job(db_session, owner.id)

    assert service.cancel_job(db_session, other.id, job.id) is None
