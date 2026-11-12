"""/voices/clones: enrolment, ownership, consent, limits, deletion, and /voices listing."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest
import soundfile as sf
from audio_helpers import speech_like, wav_bytes

import book2audio.api.auth.rate_limit as rate_limit
from book2audio.api.voices import cloning, routes
from book2audio.db.models import ClonedVoice, User
from book2audio.db.worker_heartbeat import record_heartbeat
from book2audio.tts.clone_engine import CLONE_CAPABILITY


class FakeStore:
    """An in-memory stand-in for object storage."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_deletes = False

    def read_object(self, key, *, max_bytes):
        data = self.objects.get(key)
        if data is not None and len(data) > max_bytes:
            raise ValueError("too big")
        return data

    def upload_bytes(self, data, key, *, content_type=None):
        self.objects[key] = data

    def delete_objects(self, keys):
        if self.fail_deletes:
            raise RuntimeError("storage down")
        for key in keys:
            self.objects.pop(key, None)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(cloning, "read_object", fake.read_object)
    monkeypatch.setattr(cloning, "upload_bytes", fake.upload_bytes)
    monkeypatch.setattr(cloning, "delete_objects", fake.delete_objects)
    monkeypatch.setattr(routes, "object_exists", lambda key: False)
    monkeypatch.setattr(routes, "presign_get", lambda key, expires=600, **kw: f"https://s/{key}")
    monkeypatch.setattr(routes, "presign_put", lambda key, expires=600: f"https://put/{key}")
    return fake


def _auth(client, email="voice@example.com"):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _user_id(db_session, email="voice@example.com") -> uuid.UUID:
    return db_session.query(User).filter(User.email == email).one().id


def _stage(store, user_id, voice_id, data=None) -> None:
    store.objects[cloning.staging_key(user_id, voice_id)] = data or wav_bytes(speech_like(15))


def _enrol_body(voice_id, **overrides):
    body = {
        "voice_id": str(voice_id),
        "name": "My voice",
        "gender": "f",
        "script_id": "lighthouse",
        "consent": True,
        "consent_version": cloning.CONSENT_VERSION,
    }
    return {**body, **overrides}


def _enrol(client, headers, db_session, store, **overrides):
    voice_id = uuid.uuid4()
    _stage(store, _user_id(db_session, "voice@example.com"), voice_id)
    response = client.post("/voices/clones", json=_enrol_body(voice_id, **overrides), headers=headers)
    return voice_id, response


# --- enrolment -----------------------------------------------------------------------------


def test_enrolling_a_good_recording_stores_a_private_normalized_copy_and_the_consent(
    client, db_session, store
):
    headers = _auth(client)
    user_id = _user_id(db_session)

    voice_id, response = _enrol(client, headers, db_session, store)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == f"clone:{voice_id}"
    assert body["name"] == "My voice" and body["gender"] == "f"
    final = f"voice-refs/{user_id}/{voice_id}.wav"
    assert final in store.objects
    assert cloning.staging_key(user_id, voice_id) not in store.objects  # staging copy is gone
    info = sf.info(io.BytesIO(store.objects[final]))
    assert (info.format, info.channels) == ("WAV", 1)

    row = db_session.get(ClonedVoice, voice_id)
    assert row.user_id == user_id
    assert row.ref_text == cloning.READING_SCRIPTS["lighthouse"]
    assert row.consent_version == cloning.CONSENT_VERSION
    assert row.consent_at is not None
    assert row.ref_object_key == final
    assert len(row.ref_sha256) == 64


@pytest.mark.parametrize(
    "overrides",
    [{"consent": False}, {"consent_version": "an-old-version"}],
)
def test_without_current_consent_nothing_is_saved(client, db_session, store, overrides):
    headers = _auth(client)

    voice_id, response = _enrol(client, headers, db_session, store, **overrides)

    assert response.status_code == 422
    assert db_session.query(ClonedVoice).count() == 0
    assert not any(key.startswith("voice-refs/") for key in store.objects)


def test_an_unknown_reading_script_is_refused(client, db_session, store):
    headers = _auth(client)

    _, response = _enrol(client, headers, db_session, store, script_id="made-up")

    assert response.status_code == 422


def test_a_recording_that_was_never_uploaded_is_refused(client, db_session, store):
    headers = _auth(client)

    response = client.post(
        "/voices/clones", json=_enrol_body(uuid.uuid4()), headers=headers
    )

    assert response.status_code == 422
    assert "not uploaded" in response.json()["detail"]


def test_a_bad_recording_is_refused_server_side_and_the_upload_is_discarded(
    client, db_session, store
):
    headers = _auth(client)
    user_id = _user_id(db_session)
    voice_id = uuid.uuid4()
    _stage(store, user_id, voice_id, wav_bytes(speech_like(4)))  # the browser "checked" it; we do not trust that

    response = client.post("/voices/clones", json=_enrol_body(voice_id), headers=headers)

    assert response.status_code == 422
    assert "too short" in response.json()["detail"]
    assert cloning.staging_key(user_id, voice_id) not in store.objects
    assert db_session.query(ClonedVoice).count() == 0


def test_garbage_uploaded_as_a_recording_is_refused(client, db_session, store):
    headers = _auth(client)
    voice_id = uuid.uuid4()
    _stage(store, _user_id(db_session), voice_id, b"MZ\x90\x00 not a wav")

    response = client.post("/voices/clones", json=_enrol_body(voice_id), headers=headers)

    assert response.status_code == 422


def test_a_user_cannot_enrol_a_recording_that_sits_under_another_users_prefix(
    client, db_session, store
):
    owner_headers = _auth(client, "owner@example.com")
    attacker_headers = _auth(client, "attacker@example.com")
    owner_id = _user_id(db_session, "owner@example.com")
    voice_id = uuid.uuid4()
    _stage(store, owner_id, voice_id)  # the owner's upload

    response = client.post(
        "/voices/clones", json=_enrol_body(voice_id), headers=attacker_headers
    )

    assert response.status_code == 422  # looks under the attacker's own prefix: nothing there
    assert db_session.query(ClonedVoice).count() == 0
    assert cloning.staging_key(owner_id, voice_id) in store.objects
    del owner_headers


def test_a_voice_id_can_only_be_enrolled_once(client, db_session, store):
    headers = _auth(client)
    voice_id, first = _enrol(client, headers, db_session, store)
    _stage(store, _user_id(db_session), voice_id)

    second = client.post("/voices/clones", json=_enrol_body(voice_id), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409


def test_a_user_can_keep_only_a_few_voices(client, db_session, store):
    headers = _auth(client)
    for _ in range(cloning.MAX_CLONES_PER_USER):
        assert _enrol(client, headers, db_session, store)[1].status_code == 201

    _, response = _enrol(client, headers, db_session, store)

    assert response.status_code == 409
    assert "Delete one" in response.json()["detail"]


def test_enrolment_and_upload_urls_are_rate_limited(client, db_session, store, monkeypatch):
    headers = _auth(client)
    monkeypatch.setattr(rate_limit._voice_enrolment_per_user_limiter, "max_attempts", 2)

    statuses = [
        client.post("/voices/clones/upload-url", headers=headers).status_code for _ in range(3)
    ]

    assert statuses == [200, 200, 429]


def test_the_upload_url_points_at_a_staging_key_under_the_users_own_prefix(
    client, db_session, store
):
    headers = _auth(client)
    user_id = _user_id(db_session)

    body = client.post("/voices/clones/upload-url", headers=headers).json()

    assert body["upload_url"] == f"https://put/voice-uploads/{user_id}/{body['voice_id']}.wav"


def test_the_clone_endpoints_need_a_signed_in_user(client, store):
    assert client.get("/voices/clones").status_code == 401
    assert client.post("/voices/clones/upload-url").status_code == 401
    assert client.get("/voices/clones/options").status_code == 401
    assert client.delete(f"/voices/clones/{uuid.uuid4()}").status_code == 401


def test_the_options_carry_the_scripts_the_consent_text_and_the_rules(client, store):
    headers = _auth(client)

    body = client.get("/voices/clones/options", headers=headers).json()

    assert {s["id"] for s in body["scripts"]} == set(cloning.READING_SCRIPTS)
    assert body["consent_version"] == cloning.CONSENT_VERSION
    assert "my own voice" in body["consent_text"]
    assert (body["min_duration_s"], body["min_sample_rate"]) == (10.0, 16000)
    assert body["clone_worker_online"] is False


def test_every_reading_script_is_long_enough_for_twenty_seconds_of_speech():
    for text in cloning.READING_SCRIPTS.values():
        assert 55 <= len(text.split()) <= 110  # about 20 to 35 seconds read naturally


# --- listing -------------------------------------------------------------------------------


def test_the_voice_list_includes_only_the_users_own_cloned_voices(client, db_session, store):
    mine = _auth(client, "voice@example.com")
    theirs = _auth(client, "other@example.com")
    my_id, _ = _enrol(client, mine, db_session, store)
    other_user = _user_id(db_session, "other@example.com")
    other_voice = uuid.uuid4()
    _stage(store, other_user, other_voice)
    client.post("/voices/clones", json=_enrol_body(other_voice, name="Theirs"), headers=theirs)

    body = client.get("/voices", headers=mine).json()

    clones = [v for v in body["voices"] if v["provider"] == "clone"]
    assert [v["id"] for v in clones] == [f"clone:{my_id}"]
    assert clones[0]["name"] == "My voice" and clones[0]["gender"] == "female"
    assert all(v["provider"] == "kokoro" for v in body["voices"] if v["provider"] != "clone")


def test_a_cloned_voice_is_unavailable_until_a_capable_worker_is_online(
    client, db_session, store
):
    headers = _auth(client)
    _enrol(client, headers, db_session, store)

    offline = client.get("/voices", headers=headers).json()
    record_heartbeat(db_session, "worker-1", [CLONE_CAPABILITY])
    online = client.get("/voices", headers=headers).json()

    def clone_of(body):
        return next(v for v in body["voices"] if v["provider"] == "clone")

    assert (offline["clone_worker_online"], clone_of(offline)["available"]) == (False, False)
    assert (online["clone_worker_online"], clone_of(online)["available"]) == (True, True)
    assert all(v["available"] for v in online["voices"] if v["provider"] == "kokoro")


def test_a_worker_without_the_clone_engine_does_not_make_clones_available(
    client, db_session, store
):
    headers = _auth(client)
    _enrol(client, headers, db_session, store)
    record_heartbeat(db_session, "plain-worker", [])

    assert client.get("/voices", headers=headers).json()["clone_worker_online"] is False


def test_a_stale_heartbeat_counts_as_offline(client, db_session, store):
    from datetime import timedelta

    from book2audio.db.models import WorkerHeartbeat

    headers = _auth(client)
    db_session.add(
        WorkerHeartbeat(
            worker_id="old",
            capabilities=[CLONE_CAPABILITY],
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
    )
    db_session.commit()

    assert client.get("/voices", headers=headers).json()["clone_worker_online"] is False


def test_the_clone_list_shows_only_the_users_own_voices(client, db_session, store):
    mine = _auth(client, "voice@example.com")
    other = _auth(client, "other@example.com")
    _enrol(client, mine, db_session, store)

    assert len(client.get("/voices/clones", headers=mine).json()) == 1
    assert client.get("/voices/clones", headers=other).json() == []


# --- deletion ------------------------------------------------------------------------------


def test_deleting_a_voice_removes_the_row_and_the_recording(client, db_session, store):
    headers = _auth(client)
    user_id = _user_id(db_session)
    voice_id, _ = _enrol(client, headers, db_session, store)

    response = client.delete(f"/voices/clones/{voice_id}", headers=headers)

    assert response.status_code == 204
    assert db_session.query(ClonedVoice).count() == 0
    assert f"voice-refs/{user_id}/{voice_id}.wav" not in store.objects


def test_someone_elses_voice_cannot_be_deleted(client, db_session, store):
    mine = _auth(client, "voice@example.com")
    other = _auth(client, "other@example.com")
    voice_id, _ = _enrol(client, mine, db_session, store)

    response = client.delete(f"/voices/clones/{voice_id}", headers=other)

    assert response.status_code == 404
    assert db_session.query(ClonedVoice).count() == 1
    assert any(key.startswith("voice-refs/") for key in store.objects)


def test_if_the_recording_cannot_be_deleted_the_voice_is_kept_so_the_user_can_retry(
    client, db_session, store
):
    headers = _auth(client)
    voice_id, _ = _enrol(client, headers, db_session, store)
    store.fail_deletes = True

    response = client.delete(f"/voices/clones/{voice_id}", headers=headers)

    assert response.status_code == 502
    assert db_session.query(ClonedVoice).count() == 1


def test_deleting_an_unknown_voice_is_a_404(client, store):
    headers = _auth(client)

    assert client.delete(f"/voices/clones/{uuid.uuid4()}", headers=headers).status_code == 404


def test_names_are_cleaned_of_control_characters_and_extra_spaces(client, db_session, store):
    headers = _auth(client)

    _, response = _enrol(client, headers, db_session, store, name="  Me \x00 \n  myself ")

    assert response.json()["name"] == "Me myself"


def test_a_silent_recording_is_never_stored_even_with_valid_consent(client, db_session, store):
    headers = _auth(client)
    voice_id = uuid.uuid4()
    _stage(store, _user_id(db_session), voice_id, wav_bytes(np.zeros(24000 * 15, dtype=np.float32)))

    response = client.post("/voices/clones", json=_enrol_body(voice_id), headers=headers)

    assert response.status_code == 422
    assert not any(key.startswith("voice-refs/") for key in store.objects)
