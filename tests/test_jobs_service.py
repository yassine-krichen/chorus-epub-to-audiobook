"""Unit tests for book2audio.api.jobs.service against a SQLite-backed session.

Cloud Run is never called for real: run_job_execution/get_execution are
monkeypatched so these tests exercise only book2audio's own logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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


# --- worker_mode(): cloud_run (default) vs local dispatch ------------------


def test_worker_mode_defaults_to_cloud_run_when_the_env_var_is_unset(monkeypatch):
    monkeypatch.delenv("WORKER_MODE", raising=False)

    assert service.worker_mode() == "cloud_run"


def test_worker_mode_returns_local_when_explicitly_set(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "local")

    assert service.worker_mode() == "local"


def test_worker_mode_raises_for_an_unrecognized_value(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "on-prem")

    with pytest.raises(RuntimeError, match="WORKER_MODE"):
        service.worker_mode()


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


def test_create_job_persists_thumbnails_true_in_the_jsonb_config(db_session, monkeypatch):
    """Regression test: JobConfig previously had no thumbnails field, so
    Config.thumbnails (worker/entrypoint.py's _build_config) could never be
    set to True by an API client -- the feature was unreachable."""
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "exec-2"}}),
    )
    user = _make_user(db_session)
    payload = CreateJobRequest(
        epub_object_key="uploads/book.epub",
        title="A Book",
        config=JobConfig(thumbnails=True),
    )

    job = service.create_job(db_session, user.id, payload)

    assert job.config["thumbnails"] is True


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


def test_create_job_in_local_mode_never_triggers_cloud_run_and_stays_queued(db_session, monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "local")
    run_job_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "run_job_execution", run_job_execution)
    user = _make_user(db_session)
    payload = CreateJobRequest(epub_object_key="uploads/book.epub", title="A Book")

    job = service.create_job(db_session, user.id, payload)

    run_job_execution.assert_not_called()
    assert job.status == "queued"
    assert job.cloud_run_execution_name is None
    assert job.started_at is None


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


# --- _reconcile_if_stale: local-mode branch ---------------------------------
#
# A local-mode job has no cloud_run_execution_name to poll -- if its
# heartbeat goes stale, the only reasonable conclusion is that the local
# worker crashed, so it's marked failed directly instead of calling Cloud Run.


def test_get_job_in_local_mode_marks_a_stale_running_job_with_no_execution_name_failed(
    db_session, monkeypatch
):
    monkeypatch.setenv("WORKER_MODE", "local")
    get_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "get_execution", get_execution)
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        cloud_run_execution_name=None,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "failed"
    assert result.error_message
    assert result.finished_at is not None
    get_execution.assert_not_called()


def test_get_job_in_local_mode_leaves_a_freshly_heartbeating_job_running(db_session, monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "local")
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        cloud_run_execution_name=None,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "running"


def test_get_job_in_cloud_run_mode_never_applies_the_local_stale_branch_even_with_no_execution_name(
    db_session, monkeypatch
):
    """The local-mode reconciliation branch must be strictly gated on
    worker_mode() -- a running job with no cloud_run_execution_name
    (shouldn't happen in practice under cloud_run mode) must be left alone,
    not marked failed, and Cloud Run must never be called since there's no
    execution to poll."""
    get_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "get_execution", get_execution)
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        cloud_run_execution_name=None,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    result = service.get_job(db_session, user.id, job.id)

    assert result.status == "running"
    get_execution.assert_not_called()


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


def test_cancel_job_in_cloud_run_mode_does_not_set_cancel_requested_at(db_session, monkeypatch):
    monkeypatch.setattr(service.cloud_run, "cancel_execution", MagicMock(return_value={}))
    user = _make_user(db_session)
    job = _make_job(db_session, user.id, status="running")

    result = service.cancel_job(db_session, user.id, job.id)

    assert result.status == "canceled"
    assert result.cancel_requested_at is None


def test_cancel_job_in_local_mode_sets_cancel_requested_at_and_never_calls_cloud_run(
    db_session, monkeypatch
):
    monkeypatch.setenv("WORKER_MODE", "local")
    cancel_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "cancel_execution", cancel_execution)
    user = _make_user(db_session)
    job = _make_job(db_session, user.id, status="running", cloud_run_execution_name=None)

    result = service.cancel_job(db_session, user.id, job.id)

    assert result.status == "canceled"
    assert result.cancel_requested_at is not None
    assert result.finished_at is not None
    cancel_execution.assert_not_called()


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


# --- _to_part_progress / _to_chapter_progress ----------------------------


def test_to_part_progress_renders_the_download_filename_from_the_job_config_template(monkeypatch):
    presign_get = MagicMock(return_value="https://signed-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    job = SimpleNamespace(
        title="My Book",
        config={"filename_template": "{title} - Part {part:02d}"},
        artifacts_swept_at=None,
    )
    part = {
        "part_number": 3,
        "first_index": 1,
        "last_index": 5,
        "r2_key": "jobs/1/part-03.m4b",
        "status": "done",
    }

    result = service._to_part_progress(part, job)

    assert result.download_url == "https://signed-url"
    presign_get.assert_called_once_with(
        "jobs/1/part-03.m4b", download_filename="My Book - Part 03.m4b"
    )


def test_to_part_progress_omits_a_download_filename_when_no_template_is_configured(monkeypatch):
    presign_get = MagicMock(return_value="https://signed-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    job = SimpleNamespace(title="My Book", config={}, artifacts_swept_at=None)
    part = {
        "part_number": 1,
        "first_index": 1,
        "last_index": 1,
        "r2_key": "jobs/1/part-01.m4b",
        "status": "done",
    }

    service._to_part_progress(part, job)

    presign_get.assert_called_once_with("jobs/1/part-01.m4b", download_filename=None)


def test_to_part_progress_does_not_presign_a_part_that_is_not_yet_done(monkeypatch):
    presign_get = MagicMock()
    monkeypatch.setattr(service, "presign_get", presign_get)
    job = SimpleNamespace(title="My Book", config={}, artifacts_swept_at=None)
    part = {
        "part_number": 1,
        "first_index": 1,
        "last_index": 1,
        "r2_key": "jobs/1/part-01.m4b",
        "status": "rendering",
    }

    result = service._to_part_progress(part, job)

    assert result.download_url is None
    presign_get.assert_not_called()


def test_to_chapter_progress_presigns_the_thumbnail_url_when_status_is_ready(monkeypatch):
    presign_get = MagicMock(return_value="https://signed-thumb-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    chapter = {
        "index": 1,
        "title": "One",
        "status": "done",
        "thumbnail_status": "ready",
        "thumbnail_r2_key": "jobs/1/thumbnails/chapter-0001.png",
    }
    job = SimpleNamespace(artifacts_swept_at=None)

    result = service._to_chapter_progress(chapter, job)

    assert result.thumbnail_url == "https://signed-thumb-url"
    presign_get.assert_called_once_with("jobs/1/thumbnails/chapter-0001.png")


def test_to_chapter_progress_leaves_thumbnail_fields_none_for_a_chapter_with_no_thumbnail(
    monkeypatch,
):
    presign_get = MagicMock()
    monkeypatch.setattr(service, "presign_get", presign_get)
    chapter = {"index": 1, "title": "One", "status": "done"}
    job = SimpleNamespace(artifacts_swept_at=None)

    result = service._to_chapter_progress(chapter, job)

    assert result.thumbnail_status is None
    assert result.thumbnail_url is None
    presign_get.assert_not_called()


def test_to_chapter_progress_does_not_presign_when_thumbnail_status_is_failed(monkeypatch):
    presign_get = MagicMock()
    monkeypatch.setattr(service, "presign_get", presign_get)
    chapter = {"index": 1, "title": "One", "status": "done", "thumbnail_status": "failed"}
    job = SimpleNamespace(artifacts_swept_at=None)

    result = service._to_chapter_progress(chapter, job)

    assert result.thumbnail_status == "failed"
    assert result.thumbnail_url is None
    presign_get.assert_not_called()


# --- artifacts_swept_at guard: never hand out a URL to a deleted object ---
#
# Core correctness guarantee of the cleanup sweep feature: once a job's R2
# artifacts have been deleted, no part/chapter of that job may ever get a
# presigned URL again -- regardless of what status is still stored on the
# part/chapter itself (a stale "done"/"ready" status must not matter).


def test_to_part_progress_omits_download_url_once_the_jobs_artifacts_are_swept(monkeypatch):
    presign_get = MagicMock(return_value="https://signed-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    job = SimpleNamespace(
        title="My Book",
        config={},
        artifacts_swept_at=datetime.now(timezone.utc),
    )
    part = {
        "part_number": 1,
        "first_index": 1,
        "last_index": 1,
        "r2_key": "jobs/1/part-01.m4b",
        "status": "done",  # still stored as done -- the object itself is gone
    }

    result = service._to_part_progress(part, job)

    assert result.download_url is None
    presign_get.assert_not_called()


def test_to_chapter_progress_omits_thumbnail_url_once_the_jobs_artifacts_are_swept(monkeypatch):
    presign_get = MagicMock(return_value="https://signed-thumb-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    job = SimpleNamespace(artifacts_swept_at=datetime.now(timezone.utc))
    chapter = {
        "index": 1,
        "title": "One",
        "status": "done",
        "thumbnail_status": "ready",  # still stored as ready -- object is gone
        "thumbnail_r2_key": "jobs/1/thumbnails/chapter-0001.png",
    }

    result = service._to_chapter_progress(chapter, job)

    assert result.thumbnail_status == "ready"  # status field itself is untouched
    assert result.thumbnail_url is None
    presign_get.assert_not_called()


def test_to_detail_omits_every_download_and_thumbnail_url_once_the_jobs_artifacts_are_swept(
    db_session, monkeypatch
):
    """End-to-end through to_detail(), against a real Job row -- not just the
    private helpers in isolation."""
    presign_get = MagicMock(return_value="https://signed-url")
    monkeypatch.setattr(service, "presign_get", presign_get)
    user = _make_user(db_session)
    job = _make_job(
        db_session,
        user.id,
        status="canceled",
        artifacts_swept_at=datetime.now(timezone.utc),
        parts=[
            {
                "part_number": 1,
                "first_index": 1,
                "last_index": 1,
                "r2_key": "jobs/1/part-01.m4b",
                "status": "done",
            }
        ],
        chapters=[
            {
                "index": 1,
                "title": "One",
                "status": "done",
                "thumbnail_status": "ready",
                "thumbnail_r2_key": "jobs/1/thumbnails/chapter-0001.png",
            }
        ],
    )

    detail = service.to_detail(job)

    assert detail.parts[0].download_url is None
    assert detail.chapters[0].thumbnail_url is None
    presign_get.assert_not_called()


# --- hosted LLM attribution gate ------------------------------------------------


def _llm_payload(mode="hybrid"):
    return CreateJobRequest(
        epub_object_key="uploads/book.epub", title="A Book", config=JobConfig(attributor=mode)
    )


@pytest.mark.parametrize("mode", ["llm", "hybrid"])
def test_create_job_rejects_llm_attribution_by_default_and_creates_nothing(
    db_session, monkeypatch, mode
):
    monkeypatch.delenv("HOSTED_LLM_ATTRIBUTION", raising=False)
    run_job_execution = MagicMock()
    monkeypatch.setattr(service.cloud_run, "run_job_execution", run_job_execution)
    user = _make_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        service.create_job(db_session, user.id, _llm_payload(mode))

    assert exc_info.value.status_code == 403
    assert "not enabled" in exc_info.value.detail
    run_job_execution.assert_not_called()
    assert db_session.scalar(service.select(Job).where(Job.user_id == user.id)) is None


def test_create_job_rejects_llm_attribution_when_the_flag_is_empty(db_session, monkeypatch):
    monkeypatch.setenv("HOSTED_LLM_ATTRIBUTION", "")
    user = _make_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        service.create_job(db_session, user.id, _llm_payload())

    assert exc_info.value.status_code == 403


def test_create_job_allows_llm_attribution_when_the_flag_is_enabled(db_session, monkeypatch):
    monkeypatch.setenv("HOSTED_LLM_ATTRIBUTION", "enabled")
    monkeypatch.setenv("WORKER_MODE", "local")
    user = _make_user(db_session)

    job = service.create_job(db_session, user.id, _llm_payload("llm"))

    assert job.status == "queued"
    assert job.config["attributor"] == "llm"


@pytest.mark.parametrize("typo", ["Enabled", "ENABLED", "true", "1", "enable", "enabled ", "disabled"])
def test_a_mistyped_flag_rejects_llm_jobs_and_fails_startup(db_session, monkeypatch, typo):
    from book2audio.api.main import create_app

    monkeypatch.setenv("HOSTED_LLM_ATTRIBUTION", typo)
    monkeypatch.setenv("WORKER_MODE", "local")
    user = _make_user(db_session)

    with pytest.raises(RuntimeError, match="HOSTED_LLM_ATTRIBUTION"):
        service.create_job(db_session, user.id, _llm_payload())
    assert db_session.scalar(service.select(Job).where(Job.user_id == user.id)) is None
    with pytest.raises(RuntimeError, match="HOSTED_LLM_ATTRIBUTION"):
        create_app()


def test_heuristic_jobs_never_depend_on_the_flag(db_session, monkeypatch):
    monkeypatch.setenv("HOSTED_LLM_ATTRIBUTION", "not-a-valid-value")
    monkeypatch.setenv("WORKER_MODE", "local")
    user = _make_user(db_session)
    payload = CreateJobRequest(epub_object_key="uploads/book.epub", title="A Book")

    job = service.create_job(db_session, user.id, payload)

    assert job.status == "queued"


def test_hosted_llm_attribution_enabled_only_for_the_exact_value(monkeypatch):
    monkeypatch.delenv("HOSTED_LLM_ATTRIBUTION", raising=False)
    assert service.hosted_llm_attribution_enabled() is False
    monkeypatch.setenv("HOSTED_LLM_ATTRIBUTION", "enabled")
    assert service.hosted_llm_attribution_enabled() is True
