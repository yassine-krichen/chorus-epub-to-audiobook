"""POST /jobs/{id}/cast-review and the review window: confirm, fix, expire."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from book2audio.api.jobs import service
from book2audio.api.jobs.schemas import JobConfig
from book2audio.db.models import Job


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e2"}}),
    )
    monkeypatch.setattr(service.cloud_run, "cancel_execution", MagicMock(return_value={}))


def _headers(client, email="reviewer@example.com"):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _character(name, gender=None, voice="am_michael", source="narrator"):
    return {
        "name": name,
        "lines": 5,
        "gender": gender,
        "confidence": 0.7 if gender else 0.0,
        "needs_review": gender is None,
        "voice": voice,
        "voice_source": source,
        "sample_line": "Hm.",
        "layer_counts": {},
        "top_evidence": [],
    }


def _stored_report(monkeypatch, characters=None):
    report = {
        "voice_mode": "cast",
        "narrator_voice": "am_michael",
        "confidence_threshold": 0.6,
        "characters": characters
        or [_character("Sunny", "m", "am_adam", "gender"), _character("Echo"), _character("Rain")],
    }
    monkeypatch.setattr(
        service, "read_object", MagicMock(return_value=json.dumps(report).encode())
    )


def _parked_job(client, db_session, headers, **config) -> str:
    response = client.post(
        "/jobs",
        json={"epub_object_key": "uploads/book.epub", "title": "Book", "config": config},
        headers=headers,
    )
    job_id = response.json()["job_id"]
    job = db_session.query(Job).one()
    job.status = "awaiting_cast_review"
    job.cast_review_requested_at = datetime.now(timezone.utc)
    db_session.commit()
    return job_id


def _confirm(client, headers, job_id, characters=None):
    return client.post(
        f"/jobs/{job_id}/cast-review", json={"characters": characters or []}, headers=headers
    )


# --- the happy path -----------------------------------------------------------------------


def test_confirming_with_no_changes_resumes_the_job_without_a_second_review(
    client, db_session, monkeypatch
):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id)

    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "status": "running"}
    db_session.expire_all()
    job = db_session.query(Job).one()
    assert job.config["cast_review"] is False
    assert job.cast_review_requested_at is None
    assert job.cloud_run_execution_name.endswith("/executions/e2")


def test_the_resumed_worker_is_started_with_the_fixed_settings(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    _confirm(
        client,
        headers,
        job_id,
        [{"name": "Echo", "gender": "f", "voice": "af_sky"}, {"name": "Rain", "merge_into": "Sunny"}],
    )

    sent = json.loads(service.cloud_run.run_job_execution.call_args.args[1]["JOB_CONFIG"])
    assert sent["cast_review"] is False
    assert sent["gender_overrides"] == {"Echo": "f"}
    assert sent["voice_overrides"] == {"Echo": "af_sky"}
    assert sent["character_aliases"] == {"Rain": "Sunny"}


def test_a_character_can_be_handed_to_the_narrator(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    _confirm(client, headers, job_id, [{"name": "Echo", "read_by_narrator": True}])

    db_session.expire_all()
    assert db_session.query(Job).one().config["character_aliases"] == {"Echo": "Narrator"}


def test_merging_a_character_drops_the_gender_and_voice_set_for_it_earlier(
    client, db_session, monkeypatch
):
    headers = _headers(client)
    job_id = _parked_job(
        client,
        db_session,
        headers,
        gender_overrides={"Rain": "m"},
        voice_overrides={"Rain": "am_adam"},
    )
    _stored_report(monkeypatch)

    _confirm(client, headers, job_id, [{"name": "Rain", "merge_into": "Sunny"}])

    db_session.expire_all()
    config = db_session.query(Job).one().config
    assert config["gender_overrides"] == {}
    assert config["voice_overrides"] == {}
    assert config["character_aliases"] == {"Rain": "Sunny"}


def test_in_local_mode_the_job_is_queued_for_the_daemon_and_nothing_is_triggered(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("WORKER_MODE", "local")
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id)

    assert response.json() == {"job_id": job_id, "status": "queued"}
    service.cloud_run.run_job_execution.assert_not_called()


# --- refusals -------------------------------------------------------------------------------


def test_another_users_job_is_a_404_and_nothing_changes(client, db_session, monkeypatch):
    owner = _headers(client, "owner@example.com")
    job_id = _parked_job(client, db_session, owner)
    intruder = _headers(client, "intruder@example.com")
    _stored_report(monkeypatch)

    response = _confirm(client, intruder, job_id, [{"name": "Echo", "gender": "f"}])

    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.query(Job).one().status == "awaiting_cast_review"


def test_a_missing_token_is_rejected(client):
    response = client.post(
        "/jobs/00000000-0000-0000-0000-000000000000/cast-review", json={"characters": []}
    )

    assert response.status_code == 401


def test_a_job_that_is_not_waiting_cannot_be_confirmed(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    job = db_session.query(Job).one()
    job.status = "running"
    db_session.commit()
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id)

    assert response.status_code == 409


def test_confirming_twice_resumes_the_job_only_once(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    first = _confirm(client, headers, job_id)
    second = _confirm(client, headers, job_id)

    assert (first.status_code, second.status_code) == (200, 409)
    assert service.cloud_run.run_job_execution.call_count == 2  # the create call, then one resume


def test_a_fix_for_someone_who_is_not_in_the_cast_is_a_422(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id, [{"name": "Ghost", "gender": "f"}])

    assert response.status_code == 422
    db_session.expire_all()
    assert db_session.query(Job).one().status == "awaiting_cast_review"


def test_merging_into_someone_who_is_not_in_the_cast_is_a_422(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id, [{"name": "Echo", "merge_into": "Ghost"}])

    assert response.status_code == 422


def test_a_chain_of_merges_is_a_422_with_a_readable_message(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(
        client,
        headers,
        job_id,
        [{"name": "Echo", "merge_into": "Rain"}, {"name": "Rain", "merge_into": "Sunny"}],
    )

    assert response.status_code == 422
    assert "merged into another character" in response.json()["detail"]


@pytest.mark.parametrize(
    "edit",
    [
        {"name": "Echo", "gender": "x"},
        {"name": "Echo", "voice": "not_a_voice"},
        {"name": "Echo", "merge_into": "Rain", "gender": "f"},
        {"name": "Echo", "merge_into": "Rain", "read_by_narrator": True},
        {"name": "Echo", "read_by_narrator": True, "voice": "af_sky"},
    ],
)
def test_a_malformed_fix_is_a_422(client, db_session, monkeypatch, edit):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    assert _confirm(client, headers, job_id, [edit]).status_code == 422


def test_the_same_character_twice_is_a_422(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = _confirm(
        client, headers, job_id, [{"name": "Echo", "gender": "f"}, {"name": "Echo", "gender": "m"}]
    )

    assert response.status_code == 422


def test_a_lost_cast_report_is_a_409_that_tells_the_user_what_to_do(
    client, db_session, monkeypatch
):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    monkeypatch.setattr(service, "read_object", MagicMock(return_value=None))

    response = _confirm(client, headers, job_id)

    assert response.status_code == 409
    assert "create it again" in response.json()["detail"]


def test_if_the_worker_cannot_be_started_the_job_goes_back_to_waiting(
    client, db_session, monkeypatch
):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)
    service.cloud_run.run_job_execution.side_effect = httpx.ConnectError("down")

    response = _confirm(client, headers, job_id)

    assert response.status_code == 502
    db_session.expire_all()
    job = db_session.query(Job).one()
    assert job.status == "awaiting_cast_review"
    assert job.cast_review_requested_at is not None


# --- the review window -----------------------------------------------------------------------


def _age_review(db_session, hours):
    job = db_session.query(Job).one()
    job.cast_review_requested_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db_session.commit()


def test_the_job_detail_says_when_the_review_expires(client, db_session):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)

    detail = client.get(f"/jobs/{job_id}", headers=headers).json()

    assert detail["status"] == "awaiting_cast_review"
    expires = datetime.fromisoformat(detail["cast_review_expires_at"])
    remaining = expires - datetime.now(timezone.utc)
    assert timedelta(hours=47) < remaining <= timedelta(hours=48)


def test_a_job_that_is_not_waiting_has_no_expiry(client):
    headers = _headers(client)
    job_id = client.post(
        "/jobs", json={"epub_object_key": "uploads/book.epub", "title": "Book"}, headers=headers
    ).json()["job_id"]

    assert client.get(f"/jobs/{job_id}", headers=headers).json()["cast_review_expires_at"] is None


def test_an_unconfirmed_review_is_canceled_with_a_clear_message_when_it_lapses(
    client, db_session
):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _age_review(db_session, hours=49)

    detail = client.get(f"/jobs/{job_id}", headers=headers).json()

    assert detail["status"] == "canceled"
    assert "not confirmed in time" in detail["error_message"]
    assert detail["cast_review_expires_at"] is None


def test_the_job_list_also_cancels_a_lapsed_review(client, db_session):
    headers = _headers(client)
    _parked_job(client, db_session, headers)
    _age_review(db_session, hours=72)

    jobs = client.get("/jobs", headers=headers).json()["jobs"]

    assert [job["status"] for job in jobs] == ["canceled"]


def test_a_lapsed_review_cannot_be_confirmed_any_more(client, db_session, monkeypatch):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _age_review(db_session, hours=49)
    _stored_report(monkeypatch)

    response = _confirm(client, headers, job_id)

    assert response.status_code == 409
    service.cloud_run.run_job_execution.assert_called_once()  # only the original create


def test_the_review_window_follows_cast_review_timeout_hours(client, db_session, monkeypatch):
    monkeypatch.setenv("CAST_REVIEW_TIMEOUT_HOURS", "2")
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)
    _age_review(db_session, hours=3)

    assert client.get(f"/jobs/{job_id}", headers=headers).json()["status"] == "canceled"


def test_the_user_can_cancel_a_job_that_is_waiting_for_review(client, db_session):
    headers = _headers(client)
    job_id = _parked_job(client, db_session, headers)

    assert client.delete(f"/jobs/{job_id}", headers=headers).status_code == 204

    db_session.expire_all()
    assert db_session.query(Job).one().status == "canceled"


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5", "721"])
def test_a_bad_timeout_setting_is_refused_at_startup(monkeypatch, value):
    monkeypatch.setenv("CAST_REVIEW_TIMEOUT_HOURS", value)

    with pytest.raises(RuntimeError, match="CAST_REVIEW_TIMEOUT_HOURS"):
        service.cast_review_timeout()


def test_the_default_timeout_is_two_days(monkeypatch):
    monkeypatch.delenv("CAST_REVIEW_TIMEOUT_HOURS", raising=False)

    assert service.cast_review_timeout() == timedelta(hours=48)


# --- job settings -----------------------------------------------------------------------------


def test_review_is_on_by_default_for_character_voices():
    assert JobConfig().cast_review is True


def test_a_narrator_only_job_has_nothing_to_review():
    assert JobConfig(voice_mode="narrator_only", cast_review=True).cast_review is False


def test_a_job_can_skip_the_review():
    assert JobConfig(cast_review=False).cast_review is False


def test_job_settings_refuse_a_chain_of_merges():
    with pytest.raises(ValueError, match="merged into another character"):
        JobConfig(character_aliases={"A": "B", "B": "C"})
