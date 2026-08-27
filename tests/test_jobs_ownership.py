"""Integration tests for job ownership enforcement over HTTP.

Cloud Run triggering is monkeypatched at the service layer so job creation
never makes a real network/GCP call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from book2audio.api.jobs import service


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e1"}}),
    )
    monkeypatch.setattr(service.cloud_run, "cancel_execution", MagicMock(return_value={}))


def _signed_up_user(client, email):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _create_job(client, headers, title="My Book"):
    response = client.post(
        "/jobs",
        json={"epub_object_key": "uploads/book.epub", "title": title},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["job_id"]


def test_owner_can_fetch_their_own_job(client):
    headers = _signed_up_user(client, "owner@example.com")
    job_id = _create_job(client, headers)

    response = client.get(f"/jobs/{job_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == job_id


def test_fetching_another_users_job_returns_404_not_403(client):
    owner_headers = _signed_up_user(client, "owner2@example.com")
    job_id = _create_job(client, owner_headers)
    intruder_headers = _signed_up_user(client, "intruder@example.com")

    response = client.get(f"/jobs/{job_id}", headers=intruder_headers)

    assert response.status_code == 404


def test_deleting_another_users_job_returns_404_not_403(client):
    owner_headers = _signed_up_user(client, "owner3@example.com")
    job_id = _create_job(client, owner_headers)
    intruder_headers = _signed_up_user(client, "intruder2@example.com")

    response = client.delete(f"/jobs/{job_id}", headers=intruder_headers)

    assert response.status_code == 404


def test_deleting_another_users_job_does_not_cancel_it(client):
    owner_headers = _signed_up_user(client, "owner4@example.com")
    job_id = _create_job(client, owner_headers)
    intruder_headers = _signed_up_user(client, "intruder3@example.com")

    client.delete(f"/jobs/{job_id}", headers=intruder_headers)

    owned_view = client.get(f"/jobs/{job_id}", headers=owner_headers)
    assert owned_view.json()["status"] != "canceled"


def test_owner_can_cancel_their_own_job(client):
    headers = _signed_up_user(client, "owner5@example.com")
    job_id = _create_job(client, headers)

    response = client.delete(f"/jobs/{job_id}", headers=headers)

    assert response.status_code == 204
    assert client.get(f"/jobs/{job_id}", headers=headers).json()["status"] == "canceled"


def test_fetching_a_job_without_a_bearer_token_returns_401(client):
    headers = _signed_up_user(client, "owner6@example.com")
    job_id = _create_job(client, headers)

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 401


def test_listing_jobs_only_returns_the_current_users_jobs(client):
    owner_headers = _signed_up_user(client, "owner7@example.com")
    other_headers = _signed_up_user(client, "other7@example.com")
    _create_job(client, owner_headers, title="Owner's book")
    _create_job(client, other_headers, title="Other's book")

    response = client.get("/jobs", headers=owner_headers)

    body = response.json()
    assert body["total"] == 1
    assert [j["title"] for j in body["jobs"]] == ["Owner's book"]
