"""A job can only be created from an upload the user made themselves."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from book2audio.api.jobs import service
from book2audio.api.jobs.schemas import CreateJobRequest, JobConfig, UploadUrlRequest
from book2audio.api.jobs.uploads import require_owned_upload, upload_key, validate_upload_filename
from book2audio.db.models import Job

pytestmark = pytest.mark.real_upload_guard


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e1"}}),
    )


@pytest.fixture
def account(client):
    email = "uploader@example.com"
    registered = client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"headers": {"Authorization": f"Bearer {token}"}, "id": registered.json()["id"]}


def _create(client, account, key, **extra):
    return client.post(
        "/jobs",
        json={"epub_object_key": key, "title": "Book", **extra},
        headers=account["headers"],
    )


# --- the guard ----------------------------------------------------------------------------


def test_a_key_made_by_upload_url_for_this_user_is_accepted():
    user_id = uuid.uuid4()

    require_owned_upload(user_id, upload_key(user_id, "My Book (1).epub"))  # must not raise


def test_unicode_file_names_are_accepted():
    user_id = uuid.uuid4()

    require_owned_upload(user_id, upload_key(user_id, "Café – 第一章.epub"))  # must not raise


@pytest.mark.parametrize(
    "make_key",
    [
        lambda me, other: upload_key(other, "book.epub"),  # someone else's upload
        lambda me, other: f"jobs/{uuid.uuid4()}/part-01.m4b",  # another artifact
        lambda me, other: f"jobs/{uuid.uuid4()}/state.json",
        lambda me, other: "voice-samples/am_adam.wav",
        lambda me, other: f"uploads/{me}/book.epub",  # not the shape upload-url hands out
        lambda me, other: f"uploads/{me}/{uuid.uuid4()}-a/b.epub",
        lambda me, other: f"uploads/{me}/{uuid.uuid4()}-..",
        lambda me, other: f"uploads/{me}/../{other}/{uuid.uuid4()}-book.epub",
        lambda me, other: f"uploads/{me}/{uuid.uuid4()}-book.epub\n",
        lambda me, other: f"uploads/{me}/{uuid.uuid4()}-",
        lambda me, other: f"/uploads/{me}/{uuid.uuid4()}-book.epub",
        lambda me, other: f"uploads/{str(me).upper()}/{uuid.uuid4()}-book.epub",
    ],
)
def test_any_other_key_is_a_404(make_key):
    me, other = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(service.HTTPException) as refused:
        require_owned_upload(me, make_key(me, other))

    assert refused.value.status_code == 404
    assert refused.value.detail == "Upload not found"


# --- create_job ---------------------------------------------------------------------------


def test_a_job_can_be_created_from_the_users_own_upload(client, account):
    key = upload_key(uuid.UUID(account["id"]), "book.epub")

    response = _create(client, account, key)

    assert response.status_code == 201


def test_another_users_upload_cannot_be_used_and_no_job_is_made(client, db_session, account):
    key = upload_key(uuid.uuid4(), "book.epub")

    response = _create(client, account, key)

    assert response.status_code == 404
    assert db_session.query(Job).count() == 0
    service.cloud_run.run_job_execution.assert_not_called()


def test_a_job_artifact_key_cannot_be_used_as_the_book(client, db_session, account):
    response = _create(client, account, f"jobs/{uuid.uuid4()}/part-01.m4b")

    assert response.status_code == 404
    assert db_session.query(Job).count() == 0


def test_an_overlong_key_is_a_422(client, account):
    response = _create(client, account, "uploads/" + "x" * 700)

    assert response.status_code == 422


def test_an_empty_key_is_a_422(client, account):
    assert _create(client, account, "").status_code == 422


def test_the_key_from_the_upload_url_endpoint_always_passes_the_guard(client, account, monkeypatch):
    monkeypatch.setattr("book2audio.api.jobs.routes.presign_put", lambda key: f"https://r2/{key}")

    response = client.post(
        "/jobs/upload-url", json={"filename": "Shadow Slave (001-100).epub"}, headers=account["headers"]
    )

    key = response.json()["object_key"]
    require_owned_upload(uuid.UUID(account["id"]), key)  # must not raise
    assert _create(client, account, key).status_code == 201


# --- file names ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["a/b.epub", "..\\x.epub", "../x.epub", "a\x00b.epub", "a\nb.epub", "a\x7fb.epub", "..", ".", "   "],
)
def test_a_file_name_that_could_change_the_key_is_refused(client, account, filename):
    response = client.post(
        "/jobs/upload-url", json={"filename": filename}, headers=account["headers"]
    )

    assert response.status_code == 422


def test_a_normal_file_name_is_kept_as_it_is():
    assert validate_upload_filename("Book - Part 1 (2022).epub") == "Book - Part 1 (2022).epub"


def test_upload_url_request_rejects_a_slash():
    with pytest.raises(ValidationError):
        UploadUrlRequest(filename="a/b.epub")


# --- character_aliases and voice_overrides on a new job -------------------------------------


def test_a_new_job_cannot_carry_character_merges():
    with pytest.raises(ValidationError, match="cast review"):
        CreateJobRequest(
            epub_object_key=upload_key(uuid.uuid4(), "b.epub"),
            title="Book",
            config=JobConfig(character_aliases={"Rain": "Cassie"}),
        )


def test_the_job_settings_themselves_still_hold_merges_for_the_review():
    assert JobConfig(character_aliases={"Rain": "Cassie"}).character_aliases == {"Rain": "Cassie"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"x" * 81: "am_adam"},
        {"": "am_adam"},
        {"Kai": "not_a_voice"},
        {f"Name {i}": "am_adam" for i in range(101)},
    ],
)
def test_bad_voice_overrides_are_refused(overrides):
    with pytest.raises(ValidationError):
        JobConfig(voice_overrides=overrides)


def test_good_voice_overrides_are_kept():
    assert JobConfig(voice_overrides={"Kai": "am_adam"}).voice_overrides == {"Kai": "am_adam"}
