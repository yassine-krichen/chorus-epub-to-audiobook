"""Jobs that name a cloned voice: ownership, worker gating, the requires_clone flag."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from book2audio.api.jobs import service
from book2audio.db.models import ClonedVoice, Job, User
from book2audio.db.worker_heartbeat import record_heartbeat
from book2audio.tts.clone_engine import CLONE_CAPABILITY
from book2audio.tts.voices import clone_voice_id


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "executions/e"}}),
    )


def _auth(client, email):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _voice_for(db_session, email) -> uuid.UUID:
    user = db_session.query(User).filter(User.email == email).one()
    voice = ClonedVoice(
        user_id=user.id,
        name="Mine",
        gender="m",
        ref_object_key=f"voice-refs/{user.id}/x.wav",
        ref_text="text",
        ref_sha256="0" * 64,
        duration_s=15.0,
        consent_at=datetime.now(timezone.utc),
        consent_version="v1",
    )
    db_session.add(voice)
    db_session.commit()
    return voice.id


def _create(client, headers, **config):
    return client.post(
        "/jobs",
        json={"epub_object_key": "uploads/book.epub", "title": "Book", "config": config},
        headers=headers,
    )


@pytest.fixture
def local_worker_with_clone(monkeypatch, db_session):
    monkeypatch.setenv("WORKER_MODE", "local")
    record_heartbeat(db_session, "w1", [CLONE_CAPABILITY])


def test_a_job_with_the_users_own_cloned_narrator_is_queued_and_flagged(
    client, db_session, local_worker_with_clone
):
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")

    response = _create(client, headers, narrator_voice=clone_voice_id(voice))

    assert response.status_code == 201
    job = db_session.query(Job).one()
    assert job.requires_clone is True and job.status == "queued"
    assert job.config["narrator_voice"] == f"clone:{voice}"


def test_a_cloned_voice_for_one_character_also_flags_the_job(
    client, db_session, local_worker_with_clone
):
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")

    response = _create(client, headers, voice_overrides={"Sunny": clone_voice_id(voice)})

    assert response.status_code == 201
    assert db_session.query(Job).one().requires_clone is True


def test_a_job_without_cloned_voices_is_not_flagged(client, db_session, local_worker_with_clone):
    headers = _auth(client, "a@example.com")

    assert _create(client, headers, narrator_voice="am_adam").status_code == 201

    assert db_session.query(Job).one().requires_clone is False


def test_someone_elses_cloned_voice_is_refused_like_one_that_does_not_exist(
    client, db_session, local_worker_with_clone
):
    headers = _auth(client, "a@example.com")
    _auth(client, "b@example.com")
    foreign = _voice_for(db_session, "b@example.com")

    foreign_response = _create(client, headers, narrator_voice=clone_voice_id(foreign))
    missing_response = _create(client, headers, narrator_voice=clone_voice_id(uuid.uuid4()))

    assert foreign_response.status_code == missing_response.status_code == 422
    assert foreign_response.json()["detail"] == missing_response.json()["detail"]
    assert db_session.query(Job).count() == 0


def test_a_deleted_voice_cannot_start_a_job(client, db_session, local_worker_with_clone):
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")
    db_session.query(ClonedVoice).delete()
    db_session.commit()

    assert _create(client, headers, narrator_voice=clone_voice_id(voice)).status_code == 422


def test_the_hosted_path_never_takes_a_cloned_voice_job(client, db_session, monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "cloud_run")
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")

    response = _create(client, headers, narrator_voice=clone_voice_id(voice))

    assert response.status_code == 409
    assert "local worker" in response.json()["detail"]
    assert db_session.query(Job).count() == 0
    service.cloud_run.run_job_execution.assert_not_called()


def test_without_a_capable_worker_online_the_job_is_refused_with_a_clear_reason(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("WORKER_MODE", "local")
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")
    record_heartbeat(db_session, "no-clone", [])

    response = _create(client, headers, narrator_voice=clone_voice_id(voice))

    assert response.status_code == 409
    assert "not online" in response.json()["detail"]


def test_a_malformed_clone_voice_id_is_a_validation_error(client, local_worker_with_clone):
    headers = _auth(client, "a@example.com")

    assert _create(client, headers, narrator_voice="clone:../../etc").status_code == 422
    assert _create(client, headers, voice_overrides={"X": "clone:123"}).status_code == 422


# --- the cast review ----------------------------------------------------------------------


def _parked_job(client, db_session, headers):
    job_id = _create(client, headers).json()["job_id"]
    job = db_session.query(Job).one()
    job.status = "awaiting_cast_review"
    job.cast_review_requested_at = datetime.now(timezone.utc)
    db_session.commit()
    return job_id


def _stored_report(monkeypatch):
    report = {
        "voice_mode": "cast",
        "narrator_voice": "am_michael",
        "confidence_threshold": 0.6,
        "characters": [
            {
                "name": "Echo",
                "lines": 5,
                "gender": None,
                "confidence": 0.0,
                "needs_review": True,
                "voice": "am_michael",
                "voice_source": "narrator",
                "sample_line": "Hm.",
                "layer_counts": {},
                "top_evidence": [],
            }
        ],
    }
    monkeypatch.setattr(service, "read_object", MagicMock(return_value=json.dumps(report).encode()))


def test_the_cast_review_can_pick_the_users_own_cloned_voice_for_a_character(
    client, db_session, local_worker_with_clone, monkeypatch
):
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = client.post(
        f"/jobs/{job_id}/cast-review",
        json={"characters": [{"name": "Echo", "voice": clone_voice_id(voice)}]},
        headers=headers,
    )

    assert response.status_code == 200
    db_session.expire_all()
    job = db_session.query(Job).one()
    assert job.config["voice_overrides"] == {"Echo": f"clone:{voice}"}
    assert job.requires_clone is True


def test_the_cast_review_refuses_someone_elses_cloned_voice(
    client, db_session, local_worker_with_clone, monkeypatch
):
    headers = _auth(client, "a@example.com")
    _auth(client, "b@example.com")
    foreign = _voice_for(db_session, "b@example.com")
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = client.post(
        f"/jobs/{job_id}/cast-review",
        json={"characters": [{"name": "Echo", "voice": clone_voice_id(foreign)}]},
        headers=headers,
    )

    assert response.status_code == 422
    db_session.expire_all()
    job = db_session.query(Job).one()
    assert job.status == "awaiting_cast_review"  # still waiting, nothing changed
    assert job.requires_clone is False
    assert job.config["voice_overrides"] == {}


def test_the_cast_review_on_the_hosted_path_refuses_cloned_voices(
    client, db_session, monkeypatch
):
    headers = _auth(client, "a@example.com")
    voice = _voice_for(db_session, "a@example.com")
    job_id = _parked_job(client, db_session, headers)
    _stored_report(monkeypatch)

    response = client.post(
        f"/jobs/{job_id}/cast-review",
        json={"characters": [{"name": "Echo", "voice": clone_voice_id(voice)}]},
        headers=headers,
    )

    assert response.status_code == 409
