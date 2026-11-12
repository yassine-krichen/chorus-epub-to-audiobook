"""The cast report of a job that uses a cloned voice reaches the client intact."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from test_jobs_cast_report import (  # noqa: F401 - the fixture is used by name
    _create_job,
    _report,
    _signed_up_user,
    _stub_cloud_run,
)

from book2audio.api.jobs import service

CLONED = "clone:1234abcd-1234-1234-1234-1234567890ab"


def test_a_report_with_a_cloned_voice_and_a_fallback_note_reaches_the_client(client, monkeypatch):
    headers = _signed_up_user(client, "cloned@example.com")
    job_id = _create_job(client, headers)
    report = _report(
        narrator_voice=CLONED,
        clone_fallbacks=[
            {
                "voice": CLONED,
                "reason": "the voice-cloning engine is not installed",
                "fallback_voice": "am_michael",
            }
        ],
    )
    report["characters"][0]["voice"] = CLONED
    report["characters"][0]["voice_source"] = "override"
    monkeypatch.setattr(service, "read_object", MagicMock(return_value=json.dumps(report).encode()))

    response = client.get(f"/jobs/{job_id}/cast", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["narrator_voice"] == CLONED
    assert body["characters"][0]["voice"] == CLONED
    assert body["clone_fallbacks"][0]["fallback_voice"] == "am_michael"


def test_a_report_written_before_voice_cloning_has_no_fallbacks(client, monkeypatch):
    headers = _signed_up_user(client, "oldreport@example.com")
    job_id = _create_job(client, headers)
    monkeypatch.setattr(
        service, "read_object", MagicMock(return_value=json.dumps(_report()).encode())
    )

    body = client.get(f"/jobs/{job_id}/cast", headers=headers).json()

    assert body["clone_fallbacks"] == []
