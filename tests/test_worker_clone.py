"""Worker side of voice cloning: reference clips, capability heartbeat, job claiming."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from rich.console import Console

import book2audio.worker.render as render_module
from book2audio.config import Config
from book2audio.db.models import ClonedVoice, Job, User, WorkerHeartbeat
from book2audio.db.worker_heartbeat import online_capabilities, record_heartbeat
from book2audio.tts.clone_engine import CLONE_CAPABILITY, CloneCapability
from book2audio.tts.voices import clone_voice_id
from book2audio.worker import local_daemon

AUDIO = b"RIFF-fake-audio"


def _user(db_session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="h")
    db_session.add(user)
    db_session.commit()
    return user


def _voice(db_session, user, *, key=None, sha=None, gender="f") -> ClonedVoice:
    voice_id = uuid.uuid4()
    voice = ClonedVoice(
        id=voice_id,
        user_id=user.id,
        name="Mine",
        gender=gender,
        ref_object_key=key or f"voice-refs/{user.id}/{voice_id}.wav",
        ref_text="the passage",
        ref_sha256=sha or hashlib.sha256(AUDIO).hexdigest(),
        duration_s=15.0,
        consent_at=datetime.now(timezone.utc),
        consent_version="v1",
    )
    db_session.add(voice)
    db_session.commit()
    return voice


def _job(db_session, user, **kwargs) -> Job:
    job = Job(
        user_id=user.id,
        title="Book",
        epub_object_key="uploads/book.epub",
        config={},
        status="queued",
        chapters=[],
        parts=[],
        **kwargs,
    )
    db_session.add(job)
    db_session.commit()
    return job


def _config(tmp_path, narrator="am_michael", overrides=None) -> Config:
    return Config(
        epub_path=str(tmp_path / "b.epub"),
        output_path=str(tmp_path / "b.m4b"),
        work_dir=str(tmp_path),
        narrator_voice=narrator,
        voice_overrides=overrides or {},
    )


@pytest.fixture
def downloads(monkeypatch):
    """download_capped writing AUDIO (or nothing) and recording the keys it was asked for."""
    asked: list[str] = []
    present = {"value": True}

    def fake(key, path, *, max_bytes):
        asked.append(key)
        if not present["value"]:
            return False
        with open(path, "wb") as out:
            out.write(AUDIO)
        return True

    monkeypatch.setattr(render_module, "download_capped", fake)
    fake.asked, fake.present = asked, present
    return fake


def _load(db_session, tmp_path, job, config):
    return render_module._load_clone_references(job.id, str(tmp_path), config, Console(quiet=True))


def test_the_reference_of_the_jobs_own_cloned_voice_is_downloaded_and_verified(
    db_session, tmp_path, downloads
):
    user = _user(db_session)
    voice = _voice(db_session, user, gender="f")
    job = _job(db_session, user)
    config = _config(tmp_path, narrator=clone_voice_id(voice.id))

    references = _load(db_session, tmp_path, job, config)

    reference = references[clone_voice_id(voice.id)]
    assert reference.gender == "f" and reference.ref_text == "the passage"
    assert open(reference.wav_path, "rb").read() == AUDIO
    assert downloads.asked == [voice.ref_object_key]


def test_a_job_without_cloned_voices_downloads_nothing(db_session, tmp_path, downloads):
    user = _user(db_session)
    job = _job(db_session, user)

    assert _load(db_session, tmp_path, job, _config(tmp_path)) == {}
    assert downloads.asked == []


def test_voices_named_by_characters_are_loaded_too(db_session, tmp_path, downloads):
    user = _user(db_session)
    voice = _voice(db_session, user)
    job = _job(db_session, user)
    config = _config(tmp_path, overrides={"Sunny": clone_voice_id(voice.id)})

    assert clone_voice_id(voice.id) in _load(db_session, tmp_path, job, config)


def test_a_worker_never_loads_a_voice_that_belongs_to_another_user(db_session, tmp_path, downloads):
    owner, intruder = _user(db_session), _user(db_session)
    voice = _voice(db_session, owner)
    job = _job(db_session, intruder)  # a job that names someone else's voice
    config = _config(tmp_path, narrator=clone_voice_id(voice.id))

    assert _load(db_session, tmp_path, job, config) == {}
    assert downloads.asked == []


def test_a_deleted_voice_is_simply_left_out(db_session, tmp_path, downloads):
    user = _user(db_session)
    job = _job(db_session, user)
    config = _config(tmp_path, narrator=clone_voice_id(uuid.uuid4()))

    assert _load(db_session, tmp_path, job, config) == {}


def test_a_recording_that_does_not_match_its_recorded_hash_is_not_used(
    db_session, tmp_path, downloads
):
    user = _user(db_session)
    voice = _voice(db_session, user, sha="0" * 64)
    job = _job(db_session, user)
    config = _config(tmp_path, narrator=clone_voice_id(voice.id))

    assert _load(db_session, tmp_path, job, config) == {}


def test_a_missing_recording_is_left_out(db_session, tmp_path, downloads):
    user = _user(db_session)
    voice = _voice(db_session, user)
    job = _job(db_session, user)
    downloads.present["value"] = False

    assert _load(db_session, tmp_path, job, _config(tmp_path, narrator=clone_voice_id(voice.id))) == {}


def test_a_key_outside_the_owners_prefix_is_never_read(db_session, tmp_path, downloads):
    user = _user(db_session)
    voice = _voice(db_session, user, key="uploads/someone-else/secret.wav")
    job = _job(db_session, user)

    assert _load(db_session, tmp_path, job, _config(tmp_path, narrator=clone_voice_id(voice.id))) == {}
    assert downloads.asked == []


def test_the_clone_engine_factory_starts_nothing_on_a_machine_that_cannot_clone(monkeypatch):
    monkeypatch.setattr(
        render_module, "probe_clone_capability", lambda: CloneCapability(False, "no CUDA GPU")
    )
    started = MagicMock()
    monkeypatch.setattr(render_module, "load_clone_engine", started)

    assert render_module._clone_engine_factory(Console(quiet=True))() is None
    started.assert_not_called()


def test_the_clone_engine_factory_starts_the_engine_on_a_capable_machine(monkeypatch):
    engine = object()
    monkeypatch.setattr(render_module, "probe_clone_capability", lambda: CloneCapability(True))
    monkeypatch.setattr(render_module, "load_clone_engine", lambda: engine)

    assert render_module._clone_engine_factory(Console(quiet=True))() is engine


# --- the daemon ------------------------------------------------------------------------------


def test_a_worker_without_the_clone_engine_does_not_claim_clone_jobs(db_session):
    user = _user(db_session)
    clone_job = _job(db_session, user, requires_clone=True)

    assert local_daemon._claim_next_job(db_session) is None

    db_session.refresh(clone_job)
    assert clone_job.status == "queued"


def test_a_worker_without_the_clone_engine_still_claims_plain_jobs_behind_a_clone_job(db_session):
    user = _user(db_session)
    _job(db_session, user, requires_clone=True)
    plain = _job(db_session, user)

    claimed = local_daemon._claim_next_job(db_session)

    assert claimed is not None and claimed.id == plain.id


def test_a_capable_worker_claims_clone_jobs(db_session):
    user = _user(db_session)
    clone_job = _job(db_session, user, requires_clone=True)

    claimed = local_daemon._claim_next_job(db_session, can_clone=True)

    assert claimed is not None and claimed.id == clone_job.id


def test_the_daemon_advertises_the_clone_capability_only_when_the_engine_works(monkeypatch):
    monkeypatch.setattr(
        local_daemon, "probe_clone_capability", lambda: CloneCapability(True, device="GPU")
    )
    assert local_daemon._detect_capabilities() == [CLONE_CAPABILITY]

    monkeypatch.setattr(
        local_daemon, "probe_clone_capability", lambda: CloneCapability(False, "no CUDA GPU")
    )
    assert local_daemon._detect_capabilities() == []


def test_a_heartbeat_makes_the_capability_online_and_updates_in_place(db_session):
    record_heartbeat(db_session, "w", [])
    assert online_capabilities(db_session) == set()

    record_heartbeat(db_session, "w", [CLONE_CAPABILITY])

    assert online_capabilities(db_session) == {CLONE_CAPABILITY}
    assert db_session.query(WorkerHeartbeat).count() == 1


def test_a_worker_not_heard_from_for_a_while_is_offline(db_session):
    db_session.add(
        WorkerHeartbeat(
            worker_id="gone",
            capabilities=[CLONE_CAPABILITY],
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    db_session.commit()

    assert online_capabilities(db_session) == set()


def test_pruning_cached_prompts_keeps_only_prompts_of_voices_that_still_exist(
    db_session, tmp_path, monkeypatch
):
    user = _user(db_session)
    voice = _voice(db_session, user)
    stale = "f" * 64
    (tmp_path / f"{voice.ref_sha256}.safetensors").write_bytes(b"1")
    (tmp_path / f"{stale}.safetensors").write_bytes(b"2")
    monkeypatch.setenv("CLONE_PROMPT_CACHE_DIR", str(tmp_path))

    local_daemon._prune_cached_prompts()

    assert sorted(os.listdir(tmp_path)) == [f"{voice.ref_sha256}.safetensors"]
