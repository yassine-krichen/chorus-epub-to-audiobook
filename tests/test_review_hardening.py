"""Races and limits around the cast review: who wins, what is cleaned up, what is capped."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from book2audio import review_window
from book2audio.api.jobs import service
from book2audio.api.jobs.schemas import CastReport, CastReviewRequest
from book2audio.cast_report import (
    CAST_MAX_CHARACTERS,
    CAST_NAME_MAX_LENGTH,
    CastEntry,
    cast_report_to_dict,
    read_review_report,
    trim_report_for_review,
    write_cast_report,
)
from book2audio.db.models import Job, User
from book2audio.worker import cleanup as cleanup_module
from book2audio.worker import entrypoint as entrypoint_module
from book2audio.worker import local_daemon as local_daemon_module


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e2"}}),
    )
    monkeypatch.setattr(service.cloud_run, "cancel_execution", MagicMock(return_value={}))


def _job(db_session, **overrides) -> Job:
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


def _flip_status_when_called(monkeypatch, db_session, job, new_status):
    """Make the job change status in the gap between a read and the write that follows."""
    real = service.worker_mode
    calls = []

    def flip():
        if not calls:  # only the first look, before the write under test
            calls.append(1)
            db_session.query(Job).filter(Job.id == job.id).update({"status": new_status})
            db_session.commit()
        return real()

    monkeypatch.setattr(service, "worker_mode", flip)


# --- cancel does not overwrite a finished job ------------------------------------------------


def test_cancel_leaves_a_job_that_finished_between_the_read_and_the_write(db_session, monkeypatch):
    job = _job(db_session, status="running")
    _flip_status_when_called(monkeypatch, db_session, job, "succeeded")

    service.cancel_job(db_session, job.user_id, job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "succeeded"


def test_cancel_still_cancels_a_job_waiting_for_review(db_session):
    job = _job(db_session, status="awaiting_cast_review")

    service.cancel_job(db_session, job.user_id, job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "canceled"


def test_cancel_stops_the_worker_a_confirmation_started_in_the_meantime(db_session, monkeypatch):
    job = _job(db_session, status="awaiting_cast_review")
    real = service.worker_mode
    calls = []

    def confirm_then_cancel():
        if not calls:  # only the first look, before cancel_job writes
            calls.append(1)
            db_session.query(Job).filter(Job.id == job.id).update(
                {"status": "running", "cloud_run_execution_name": "executions/started-meanwhile"}
            )
            db_session.commit()
        return real()

    monkeypatch.setattr(service, "worker_mode", confirm_then_cancel)

    service.cancel_job(db_session, job.user_id, job.id)

    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "canceled"
    service.cloud_run.cancel_execution.assert_called_once_with("executions/started-meanwhile")


# --- the workers only finish a job that is still running ---------------------------------------


@pytest.mark.parametrize("module", [entrypoint_module, local_daemon_module])
@pytest.mark.parametrize("status", ["canceled", "awaiting_cast_review", "failed"])
def test_a_worker_does_not_mark_succeeded_a_job_that_is_no_longer_running(
    db_session, module, status
):
    job = _job(db_session, status=status)

    module._mark_succeeded(job.id)

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == status
    assert refreshed.finished_at is None


@pytest.mark.parametrize("module", [entrypoint_module, local_daemon_module])
def test_a_worker_marks_a_running_job_succeeded(db_session, module):
    job = _job(db_session, status="running")

    module._mark_succeeded(job.id)

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "succeeded"
    assert refreshed.finished_at is not None


@pytest.mark.parametrize("module", [entrypoint_module, local_daemon_module])
@pytest.mark.parametrize("status", ["canceled", "succeeded"])
def test_a_late_failure_does_not_overwrite_a_canceled_or_finished_job(db_session, module, status):
    job = _job(db_session, status=status)

    module._mark_failed(job.id, "boom")

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == status
    assert refreshed.error_message is None


@pytest.mark.parametrize("module", [entrypoint_module, local_daemon_module])
def test_a_worker_failure_marks_a_running_job_failed(db_session, module):
    job = _job(db_session, status="running")

    module._mark_failed(job.id, "boom")

    db_session.expire_all()
    refreshed = db_session.get(Job, job.id)
    assert (refreshed.status, refreshed.error_message) == ("failed", "boom")


# --- confirming when the worker cannot start ---------------------------------------------------


def _report_bytes() -> bytes:
    character = {
        "name": "Echo",
        "lines": 3,
        "gender": None,
        "confidence": 0.0,
        "needs_review": True,
        "voice": "am_michael",
        "voice_source": "narrator",
        "sample_line": "Hm.",
        "layer_counts": {},
        "top_evidence": [],
    }
    return json.dumps(
        {
            "voice_mode": "cast",
            "narrator_voice": "am_michael",
            "confidence_threshold": 0.6,
            "characters": [character],
        }
    ).encode()


def test_a_failed_start_restores_the_settings_the_job_had_before_the_confirmation(
    db_session, monkeypatch
):
    job = _job(
        db_session,
        status="awaiting_cast_review",
        cast_review_requested_at=datetime.now(timezone.utc),
        config={"cast_review": True, "gender_overrides": {}},
    )
    monkeypatch.setattr(service, "read_object", MagicMock(return_value=_report_bytes()))
    service.cloud_run.run_job_execution.side_effect = httpx.ConnectError("down")
    payload = CastReviewRequest(characters=[{"name": "Echo", "gender": "f"}])

    with pytest.raises(service.HTTPException) as refused:
        service.confirm_cast_review(db_session, job.user_id, job.id, payload)

    assert refused.value.status_code == 502
    db_session.expire_all()
    restored = db_session.get(Job, job.id)
    assert restored.status == "awaiting_cast_review"
    assert restored.config == {"cast_review": True, "gender_overrides": {}}


# --- the review window --------------------------------------------------------------------------


def test_an_expiry_never_overwrites_a_confirmation_that_got_there_first(db_session):
    job = _job(
        db_session,
        status="awaiting_cast_review",
        cast_review_requested_at=datetime.now(timezone.utc) - timedelta(hours=72),
    )
    stale_view = db_session.get(Job, job.id)
    db_session.query(Job).filter(Job.id == job.id).update({"status": "running"})
    db_session.commit()

    expired = review_window.expire_if_lapsed(db_session, stale_view)

    assert expired is False
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "running"


def test_the_cleanup_sweep_cancels_an_overdue_review_nobody_opened(db_session, monkeypatch):
    monkeypatch.setattr(cleanup_module, "list_objects", MagicMock(return_value=[]))
    monkeypatch.setattr(cleanup_module, "delete_objects", MagicMock())
    overdue = _job(
        db_session,
        status="awaiting_cast_review",
        cast_review_requested_at=datetime.now(timezone.utc) - timedelta(hours=72),
    )
    fresh = _job(
        db_session,
        status="awaiting_cast_review",
        cast_review_requested_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    cleanup_module.main()

    db_session.expire_all()
    expired = db_session.get(Job, overdue.id)
    assert expired.status == "canceled"
    assert expired.finished_at is not None
    assert "not confirmed in time" in expired.error_message
    assert db_session.get(Job, fresh.id).status == "awaiting_cast_review"


def test_an_expired_review_is_swept_once_its_grace_period_has_passed(db_session, monkeypatch):
    list_objects = MagicMock(return_value=["jobs/x/cast_report.json"])
    delete_objects = MagicMock()
    monkeypatch.setattr(cleanup_module, "list_objects", list_objects)
    monkeypatch.setattr(cleanup_module, "delete_objects", delete_objects)
    monkeypatch.setenv("CLEANUP_GRACE_PERIOD_DAYS", "0")
    job = _job(
        db_session,
        status="awaiting_cast_review",
        cast_review_requested_at=datetime.now(timezone.utc) - timedelta(hours=72),
    )

    cleanup_module.main()

    db_session.expire_all()
    assert db_session.get(Job, job.id).artifacts_swept_at is not None
    delete_objects.assert_called_once_with(["jobs/x/cast_report.json"])


# --- one set of limits for the report, the API and the review ------------------------------------


def _entry(name: str, lines: int = 1, needs_review: bool = False) -> CastEntry:
    return CastEntry(
        name=name,
        lines=lines,
        gender="m",
        confidence=0.9,
        needs_review=needs_review,
        voice="am_adam",
        voice_source="gender",
        sample_line="Hm.",
    )


def _payload(entries):
    return cast_report_to_dict(entries, voice_mode="cast", narrator_voice="am_michael")


def test_the_review_copy_lists_at_most_the_busiest_characters_and_counts_the_rest():
    payload = _payload(
        [_entry(f"Name {i}", lines=1000 - i) for i in range(CAST_MAX_CHARACTERS + 7)]
    )

    trimmed = trim_report_for_review(payload)

    assert [c["name"] for c in trimmed["characters"]] == [
        f"Name {i}" for i in range(CAST_MAX_CHARACTERS)
    ]
    assert trimmed["omitted_characters"] == 7


def test_a_name_the_review_cannot_address_is_left_out_of_the_review_copy_and_counted():
    payload = _payload([_entry("Sunny"), _entry("x" * (CAST_NAME_MAX_LENGTH + 1))])

    trimmed = trim_report_for_review(payload)

    assert [c["name"] for c in trimmed["characters"]] == ["Sunny"]
    assert trimmed["omitted_characters"] == 1


def test_a_report_within_the_limits_loses_nothing():
    trimmed = trim_report_for_review(_payload([_entry("Sunny"), _entry("Cassie")]))

    assert [c["name"] for c in trimmed["characters"]] == ["Sunny", "Cassie"]
    assert trimmed["omitted_characters"] == 0


def test_the_local_cast_report_keeps_every_character(tmp_path):
    entries = [_entry(f"Name {i}") for i in range(CAST_MAX_CHARACTERS + 25)]
    entries.append(_entry("y" * 300))

    path = write_cast_report(
        str(tmp_path), entries, voice_mode="cast", narrator_voice="am_michael"
    )

    saved = json.loads(open(path, encoding="utf-8").read())
    assert len(saved["characters"]) == CAST_MAX_CHARACTERS + 26
    assert "omitted_characters" not in saved


def test_the_uploaded_copy_always_validates_against_the_api_model_whatever_the_cast_size(tmp_path):
    entries = [_entry(f"Name {i}", lines=500 - i) for i in range(250)]
    entries.append(_entry("y" * 300))
    path = write_cast_report(
        str(tmp_path), entries, voice_mode="cast", narrator_voice="am_michael"
    )

    parsed = CastReport.model_validate_json(read_review_report(path))

    assert len(parsed.characters) == CAST_MAX_CHARACTERS
    assert parsed.omitted_characters == 251 - CAST_MAX_CHARACTERS


def test_uploading_the_report_sends_the_trimmed_copy_and_leaves_the_local_file_alone(
    tmp_path, monkeypatch
):
    from book2audio.worker import progress as progress_module

    entries = [_entry(f"Name {i}") for i in range(CAST_MAX_CHARACTERS + 5)]
    path = write_cast_report(
        str(tmp_path), entries, voice_mode="cast", narrator_voice="am_michael"
    )
    sent = {}
    monkeypatch.setattr(
        progress_module,
        "upload_bytes",
        lambda data, key, content_type=None: sent.update(data=data, key=key),
    )
    job_id = uuid.uuid4()

    progress_module.build_cast_report_uploader(job_id)(path)

    assert sent["key"] == f"jobs/{job_id}/cast_report.json"
    assert len(json.loads(sent["data"])["characters"]) == CAST_MAX_CHARACTERS
    assert len(json.loads(open(path, encoding="utf-8").read())["characters"]) == (
        CAST_MAX_CHARACTERS + 5
    )


def test_the_review_request_takes_no_more_edits_than_the_report_can_list():
    edits = [{"name": f"Name {i}", "gender": "f"} for i in range(CAST_MAX_CHARACTERS + 1)]

    with pytest.raises(ValidationError):
        CastReviewRequest(characters=edits)


def test_the_api_report_model_rejects_a_name_longer_than_the_review_accepts():
    character = json.loads(_report_bytes())["characters"][0]
    character["name"] = "z" * (CAST_NAME_MAX_LENGTH + 1)

    with pytest.raises(ValidationError):
        CastReport.model_validate(
            {
                "voice_mode": "cast",
                "narrator_voice": "am_michael",
                "confidence_threshold": 0.6,
                "characters": [character],
            }
        )


# --- storage errors while reading the cast ---------------------------------------------------------


def test_a_storage_error_reading_the_cast_report_is_no_report_not_a_500(db_session, monkeypatch):
    job = _job(db_session)
    monkeypatch.setattr(
        service,
        "read_object",
        MagicMock(
            side_effect=ClientError(
                {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
                "HeadObject",
            )
        ),
    )

    assert service.get_cast_report(job) is None
