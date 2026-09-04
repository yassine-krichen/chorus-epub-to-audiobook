"""Integration tests for GET /voices against a real (SQLite-backed) DB session."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from book2audio.api.voices import routes
from book2audio.tts.voices import CHARACTER_VOICE_POOL, DEFAULT_NARRATOR_VOICE, FEMALE_VOICES, MALE_VOICES


@pytest.fixture(autouse=True)
def _no_real_r2_calls(monkeypatch):
    """Every test in this file talks to a real (SQLite) DB but must never make
    a real network call to R2/MinIO -- default to "sample doesn't exist" so
    tests that don't care about sample_url don't depend on real storage."""
    monkeypatch.setattr(routes, "object_exists", MagicMock(return_value=False))


def _auth_headers(client, email="voices@example.com"):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post(
        "/auth/login", json={"email": email, "password": "hunter2pass"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_voices_without_a_bearer_token_returns_401(client):
    response = client.get("/voices")
    assert response.status_code == 401


def test_list_voices_returns_the_narrator_voice_and_the_full_character_pool(client):
    headers = _auth_headers(client)

    response = client.get("/voices", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["narrator_voice"] == DEFAULT_NARRATOR_VOICE
    voice_ids = {v["id"] for v in body["voices"]}
    assert voice_ids == {DEFAULT_NARRATOR_VOICE, *CHARACTER_VOICE_POOL}


def test_list_voices_marks_only_the_narrator_voice_as_the_default_narrator(client):
    headers = _auth_headers(client)

    body = client.get("/voices", headers=headers).json()

    defaults = [v["id"] for v in body["voices"] if v["is_default_narrator"]]
    assert defaults == [DEFAULT_NARRATOR_VOICE]


def test_list_voices_reports_gender_matching_the_male_and_female_pools(client):
    headers = _auth_headers(client)

    body = client.get("/voices", headers=headers).json()

    by_id = {v["id"]: v["gender"] for v in body["voices"]}
    for voice in MALE_VOICES:
        assert by_id[voice] == "male"
    for voice in FEMALE_VOICES:
        assert by_id[voice] == "female"


# --- _sample_url: only presign when the sample object actually exists ------


def test_sample_url_is_null_when_the_sample_object_does_not_exist(monkeypatch):
    monkeypatch.setattr(routes, "object_exists", MagicMock(return_value=False))
    presign_get = MagicMock()
    monkeypatch.setattr(routes, "presign_get", presign_get)

    assert routes._sample_url(DEFAULT_NARRATOR_VOICE) is None
    presign_get.assert_not_called()


def test_sample_url_is_populated_when_the_sample_object_exists(monkeypatch):
    monkeypatch.setattr(routes, "object_exists", MagicMock(return_value=True))
    presign_get = MagicMock(return_value="https://signed-sample-url")
    monkeypatch.setattr(routes, "presign_get", presign_get)

    url = routes._sample_url(DEFAULT_NARRATOR_VOICE)

    assert url == "https://signed-sample-url"
    presign_get.assert_called_once_with(
        f"voice-samples/{DEFAULT_NARRATOR_VOICE}.wav",
        expires=routes._SAMPLE_URL_EXPIRY_SECONDS,
    )


def test_sample_url_is_null_and_logs_when_object_exists_raises_unexpectedly(monkeypatch, caplog):
    monkeypatch.setattr(
        routes, "object_exists", MagicMock(side_effect=RuntimeError("R2 misconfigured"))
    )
    presign_get = MagicMock()
    monkeypatch.setattr(routes, "presign_get", presign_get)

    with caplog.at_level("WARNING"):
        url = routes._sample_url(DEFAULT_NARRATOR_VOICE)

    assert url is None
    presign_get.assert_not_called()
    assert any(DEFAULT_NARRATOR_VOICE in record.message for record in caplog.records)


def test_list_voices_returns_a_sample_url_only_for_voices_whose_sample_exists(client):
    headers = _auth_headers(client)
    existing = {DEFAULT_NARRATOR_VOICE}

    def fake_object_exists(key: str) -> bool:
        voice_id = key.removeprefix("voice-samples/").removesuffix(".wav")
        return voice_id in existing

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(routes, "object_exists", fake_object_exists)
        monkeypatch.setattr(routes, "presign_get", lambda key, **kwargs: f"https://signed/{key}")
        body = client.get("/voices", headers=headers).json()

    by_id = {v["id"]: v["sample_url"] for v in body["voices"]}
    assert by_id[DEFAULT_NARRATOR_VOICE] == f"https://signed/voice-samples/{DEFAULT_NARRATOR_VOICE}.wav"
    for voice_id in CHARACTER_VOICE_POOL:
        assert by_id[voice_id] is None
