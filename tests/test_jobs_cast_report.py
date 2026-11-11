"""GET /jobs/{id}/cast: the stored cast report, validated, owner-only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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


def _signed_up_user(client, email):
    client.post("/auth/register", json={"email": email, "password": "hunter2pass"})
    token = client.post("/auth/login", json={"email": email, "password": "hunter2pass"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _create_job(client, headers):
    response = client.post(
        "/jobs", json={"epub_object_key": "uploads/book.epub", "title": "Book"}, headers=headers
    )
    return response.json()["job_id"]


def _report(**overrides) -> dict:
    report = {
        "voice_mode": "cast",
        "narrator_voice": "am_michael",
        "provider": "kokoro",
        "confidence_threshold": 0.6,
        "characters": [
            {
                "name": "Nephis",
                "lines": 90,
                "gender": "f",
                "confidence": 0.63,
                "needs_review": True,
                "layer_counts": {"pronoun": 30},
                "male_weight": 8.0,
                "female_weight": 22.0,
                "top_evidence": [
                    {
                        "layer": "pronoun",
                        "gender": "f",
                        "weight": 1.0,
                        "chapter_index": 3,
                        "snippet": "Nephis raised her sword.",
                    }
                ],
                "sample_line": "We move at dawn.",
                "voice": "af_heart",
                "voice_source": "gender",
                "provider": "kokoro",
            },
            {
                "name": "Echo",
                "lines": 4,
                "gender": None,
                "confidence": 0.0,
                "needs_review": True,
                "layer_counts": {},
                "top_evidence": [],
                "sample_line": "Hm.",
                "voice": "am_michael",
                "voice_source": "narrator",
            },
        ],
    }
    report.update(overrides)
    return report


def _stored(monkeypatch, payload: object | None) -> MagicMock:
    if payload is None:
        raw = None
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raw = json.dumps(payload).encode()
    read = MagicMock(return_value=raw)
    monkeypatch.setattr(service, "read_object", read)
    return read


def test_the_owner_gets_the_validated_cast_report(client, monkeypatch):
    headers = _signed_up_user(client, "owner@example.com")
    job_id = _create_job(client, headers)
    read = _stored(monkeypatch, _report())

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["voice_mode"] == "cast"
    assert body["narrator_voice"] == "am_michael"
    assert [c["name"] for c in body["characters"]] == ["Nephis", "Echo"]
    assert body["characters"][1]["gender"] is None
    assert read.call_args.args[0] == f"jobs/{job_id}/cast_report.json"


def test_only_the_explicit_fields_reach_the_client(client, monkeypatch):
    headers = _signed_up_user(client, "fields@example.com")
    job_id = _create_job(client, headers)
    _stored(monkeypatch, _report())

    body = client.get(f"/jobs/{job_id}/cast", headers=headers).json()

    assert "provider" not in body
    nephis = body["characters"][0]
    assert "male_weight" not in nephis
    assert "provider" not in nephis
    assert set(nephis["top_evidence"][0]) == {"layer", "gender", "snippet"}


def test_another_users_job_is_a_404_and_the_report_is_never_read(client, monkeypatch):
    owner = _signed_up_user(client, "owner2@example.com")
    job_id = _create_job(client, owner)
    intruder = _signed_up_user(client, "intruder@example.com")
    read = _stored(monkeypatch, _report())

    response = client.get(f"/jobs/{job_id}/cast", headers=intruder)

    assert response.status_code == 404
    read.assert_not_called()


def test_a_missing_token_is_rejected(client):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000/cast")

    assert response.status_code == 401


def test_a_job_that_has_no_report_yet_is_a_404(client, monkeypatch):
    headers = _signed_up_user(client, "early@example.com")
    job_id = _create_job(client, headers)
    _stored(monkeypatch, None)

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        {
            "voice_mode": "bogus",
            "narrator_voice": "am_michael",
            "confidence_threshold": 0.6,
            "characters": [],
        },
        {"voice_mode": "cast"},
        _report(characters=[{"name": "X", "lines": 1, "gender": "x"}]),
    ],
)
def test_a_corrupt_stored_report_is_a_404_not_a_500(client, monkeypatch, payload):
    headers = _signed_up_user(client, "corrupt@example.com")
    job_id = _create_job(client, headers)
    _stored(monkeypatch, payload)

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 404


def test_an_oversized_stored_report_is_a_404(client, monkeypatch):
    headers = _signed_up_user(client, "huge@example.com")
    job_id = _create_job(client, headers)
    monkeypatch.setattr(service, "read_object", MagicMock(side_effect=ValueError("too big")))

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 404


def test_a_swept_job_has_no_report_and_storage_is_not_asked(client, db_session, monkeypatch):
    headers = _signed_up_user(client, "swept@example.com")
    job_id = _create_job(client, headers)
    job = db_session.query(Job).one()
    job.artifacts_swept_at = datetime.now(timezone.utc)
    db_session.commit()
    read = _stored(monkeypatch, _report())

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 404
    read.assert_not_called()
