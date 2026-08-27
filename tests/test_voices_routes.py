"""Integration tests for GET /voices against a real (SQLite-backed) DB session."""

from __future__ import annotations

from book2audio.tts.voices import CHARACTER_VOICE_POOL, DEFAULT_NARRATOR_VOICE, FEMALE_VOICES, MALE_VOICES


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
