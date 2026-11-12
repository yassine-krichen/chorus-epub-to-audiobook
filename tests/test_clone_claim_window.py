"""Voice-cloning jobs nobody can run: they fail with a message, and deleting a voice frees them."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from book2audio import clone_claim_window as window
from book2audio.api.main import create_app
from book2audio.api.voices import cloning
from book2audio.db.models import ClonedVoice, Job, User
from book2audio.tts.voices import clone_voice_id
from book2audio.worker import cleanup as cleanup_module


def _user(db_session, email=None) -> User:
    user = User(email=email or f"{uuid.uuid4()}@example.com", password_hash="h")
    db_session.add(user)
    db_session.commit()
    return user


def _voice(db_session, user) -> ClonedVoice:
    voice_id = uuid.uuid4()
    voice = ClonedVoice(
        id=voice_id,
        user_id=user.id,
        name="Mine",
        gender="m",
        ref_object_key=f"voice-refs/{user.id}/{voice_id}.wav",
        ref_text="text",
        ref_sha256="0" * 64,
        duration_s=15.0,
        consent_at=datetime.now(timezone.utc),
        consent_version="v",
    )
    db_session.add(voice)
    db_session.commit()
    return voice


def _job(db_session, user, *, age_minutes=0, requires_clone=True, status="queued", voices=(), **kw):
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    config = {"narrator_voice": clone_voice_id(voices[0]) if voices else "am_michael"}
    if len(voices) > 1:
        config["voice_overrides"] = {"Sunny": clone_voice_id(voices[1])}
    job = Job(
        user_id=user.id,
        title="Book",
        epub_object_key="uploads/book.epub",
        config=config,
        status=status,
        requires_clone=requires_clone,
        chapters=[],
        parts=[],
        created_at=created,
        **kw,
    )
    db_session.add(job)
    db_session.commit()
    return job


# --- the timeout setting ----------------------------------------------------------------------


def test_the_default_wait_is_thirty_minutes(monkeypatch):
    monkeypatch.delenv("CLONE_CLAIM_TIMEOUT_MINUTES", raising=False)

    assert window.clone_claim_timeout() == timedelta(minutes=30)


def test_the_wait_follows_the_setting(monkeypatch):
    monkeypatch.setenv("CLONE_CLAIM_TIMEOUT_MINUTES", "5")

    assert window.clone_claim_timeout() == timedelta(minutes=5)


@pytest.mark.parametrize("value", ["0", "-3", "soon", "1441", "1.5"])
def test_a_bad_setting_is_refused_at_startup(monkeypatch, value):
    monkeypatch.setenv("CLONE_CLAIM_TIMEOUT_MINUTES", value)

    with pytest.raises(RuntimeError, match="CLONE_CLAIM_TIMEOUT_MINUTES"):
        create_app()


# --- a job nobody picked up --------------------------------------------------------------------


def test_a_clone_job_that_waited_too_long_fails_with_a_clear_message(db_session):
    user = _user(db_session)
    job = _job(db_session, user, age_minutes=45)

    assert window.fail_if_unclaimed(db_session, job) is True

    db_session.refresh(job)
    assert job.status == "failed" and job.finished_at is not None
    assert "local worker" in job.error_message and "built-in voices" in job.error_message


def test_a_clone_job_still_inside_the_wait_is_left_alone(db_session):
    job = _job(db_session, _user(db_session), age_minutes=5)

    assert window.fail_if_unclaimed(db_session, job) is False
    db_session.refresh(job)
    assert job.status == "queued"


def test_a_job_that_does_not_need_a_clone_is_never_failed_by_this(db_session):
    job = _job(db_session, _user(db_session), age_minutes=500, requires_clone=False)

    assert window.fail_if_unclaimed(db_session, job) is False


def test_a_job_a_worker_already_claimed_is_left_alone(db_session):
    job = _job(db_session, _user(db_session), age_minutes=500, status="running")

    assert window.fail_if_unclaimed(db_session, job) is False
    db_session.refresh(job)
    assert job.status == "running"


def test_a_job_put_back_in_the_queue_restarts_the_wait(db_session):
    # A confirmed cast review re-queues an old job; its heartbeat is set at that moment.
    job = _job(
        db_session, _user(db_session), age_minutes=3000, last_heartbeat_at=datetime.now(timezone.utc)
    )

    assert window.fail_if_unclaimed(db_session, job) is False


def test_reading_an_overdue_job_through_the_api_fails_it(client, db_session):
    client.post("/auth/register", json={"email": "wait@example.com", "password": "hunter2pass"})
    token = client.post(
        "/auth/login", json={"email": "wait@example.com", "password": "hunter2pass"}
    ).json()["access_token"]
    user = db_session.query(User).filter(User.email == "wait@example.com").one()
    job = _job(db_session, user, age_minutes=45)

    body = client.get(f"/jobs/{job.id}", headers={"Authorization": f"Bearer {token}"}).json()

    assert body["status"] == "failed"
    assert "picked this job up" in body["error_message"]


def test_the_cleanup_sweep_fails_overdue_clone_jobs_nobody_read(db_session, monkeypatch):
    monkeypatch.setattr(cleanup_module, "sweep_stale_voice_uploads", lambda: 0)
    user = _user(db_session)
    overdue = _job(db_session, user, age_minutes=45)
    fresh = _job(db_session, user, age_minutes=2)

    assert cleanup_module._fail_unclaimed_clone_jobs() == 1

    db_session.refresh(overdue)
    db_session.refresh(fresh)
    assert (overdue.status, fresh.status) == ("failed", "queued")


# --- deleting the last cloned voice frees the job ---------------------------------------------


@pytest.fixture
def no_storage(monkeypatch):
    monkeypatch.setattr(cloning, "delete_objects", lambda keys: None)


def test_deleting_the_only_voice_a_job_names_lets_any_worker_take_it(db_session, no_storage):
    user = _user(db_session)
    voice = _voice(db_session, user)
    queued = _job(db_session, user, voices=[voice.id])
    waiting_review = _job(db_session, user, voices=[voice.id], status="awaiting_cast_review")

    assert cloning.delete_cloned_voice(db_session, user.id, voice.id) is True

    db_session.refresh(queued)
    db_session.refresh(waiting_review)
    assert queued.requires_clone is False and waiting_review.requires_clone is False


def test_a_job_that_still_has_a_voice_keeps_needing_the_engine(db_session, no_storage):
    user = _user(db_session)
    gone, kept = _voice(db_session, user), _voice(db_session, user)
    job = _job(db_session, user, voices=[gone.id, kept.id])

    cloning.delete_cloned_voice(db_session, user.id, gone.id)

    db_session.refresh(job)
    assert job.requires_clone is True


def test_deleting_a_voice_leaves_other_users_jobs_and_finished_jobs_alone(db_session, no_storage):
    user, other = _user(db_session), _user(db_session)
    voice, theirs = _voice(db_session, user), _voice(db_session, other)
    others_job = _job(db_session, other, voices=[theirs.id])
    finished = _job(db_session, user, voices=[voice.id], status="succeeded")

    cloning.delete_cloned_voice(db_session, user.id, voice.id)

    db_session.refresh(others_job)
    db_session.refresh(finished)
    assert others_job.requires_clone is True and finished.requires_clone is True


def test_a_freed_job_is_claimed_by_a_worker_without_the_engine(db_session, no_storage):
    from book2audio.worker import local_daemon

    user = _user(db_session)
    voice = _voice(db_session, user)
    job = _job(db_session, user, voices=[voice.id])
    assert local_daemon._claim_next_job(db_session) is None  # needs the engine

    cloning.delete_cloned_voice(db_session, user.id, voice.id)

    claimed = local_daemon._claim_next_job(db_session)
    assert claimed is not None and claimed.id == job.id
