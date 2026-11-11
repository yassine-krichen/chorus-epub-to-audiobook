"""The wizard's per-character gender overrides survive job creation and reach the worker."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from book2audio.api.jobs import service
from book2audio.db.models import Job


@pytest.fixture(autouse=True)
def _stub_cloud_run(monkeypatch):
    monkeypatch.setattr(
        service.cloud_run,
        "run_job_execution",
        MagicMock(return_value={"metadata": {"name": "projects/p/locations/l/jobs/j/executions/e1"}}),
    )


def _headers(client):
    client.post("/auth/register", json={"email": "wizard@example.com", "password": "hunter2pass"})
    token = client.post(
        "/auth/login", json={"email": "wizard@example.com", "password": "hunter2pass"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create(client, headers, config):
    return client.post(
        "/jobs",
        json={"epub_object_key": "uploads/book.epub", "title": "Book", "config": config},
        headers=headers,
    )


def test_gender_overrides_are_stored_on_the_job_and_handed_to_the_worker(client, db_session):
    headers = _headers(client)

    response = _create(
        client,
        headers,
        {"gender_overrides": {"Cassie": "f"}, "voice_overrides": {"Kai": "am_adam"}},
    )

    assert response.status_code == 201
    stored = db_session.query(Job).one().config
    assert stored["gender_overrides"] == {"Cassie": "f"}
    assert stored["voice_overrides"] == {"Kai": "am_adam"}
    env = service.cloud_run.run_job_execution.call_args.args[1]
    assert '"gender_overrides": {"Cassie": "f"}' in env["JOB_CONFIG"]


@pytest.mark.parametrize("overrides", [{"Cassie": "x"}, {"": "f"}, {"x" * 81: "m"}])
def test_an_invalid_gender_override_is_rejected_before_a_job_exists(client, db_session, overrides):
    headers = _headers(client)

    response = _create(client, headers, {"gender_overrides": overrides})

    assert response.status_code == 422
    assert db_session.query(Job).count() == 0
