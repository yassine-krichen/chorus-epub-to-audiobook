"""Tests for the Cloud Run Admin API v2 REST client.

httpx and google-auth are both mocked — no real network call or GCP
credentials are used or required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from book2audio.api.jobs import cloud_run


@pytest.fixture(autouse=True)
def _stub_google_auth(monkeypatch):
    fake_credentials = MagicMock()
    fake_credentials.token = "fake-access-token"
    monkeypatch.setattr(cloud_run, "google_auth_default", MagicMock(return_value=(fake_credentials, None)))
    return fake_credentials


def _fake_response(json_body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    return response


def test_run_job_execution_posts_env_overrides_as_container_overrides(monkeypatch):
    post = MagicMock(return_value=_fake_response({"metadata": {"name": "exec-1"}}))
    monkeypatch.setattr(httpx, "post", post)

    result = cloud_run.run_job_execution(
        "projects/p/locations/l/jobs/j", {"JOB_ID": "abc", "EPUB_OBJECT_KEY": "k"}
    )

    assert result == {"metadata": {"name": "exec-1"}}
    url, kwargs = post.call_args.args[0], post.call_args.kwargs
    assert url == "https://run.googleapis.com/v2/projects/p/locations/l/jobs/j:run"
    assert kwargs["headers"] == {"Authorization": "Bearer fake-access-token"}
    env = kwargs["json"]["overrides"]["containerOverrides"][0]["env"]
    assert {"name": "JOB_ID", "value": "abc"} in env
    assert {"name": "EPUB_OBJECT_KEY", "value": "k"} in env


def test_run_job_execution_raises_on_an_http_error_status(monkeypatch):
    response = _fake_response({})
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock(status_code=500)
    )
    monkeypatch.setattr(httpx, "post", MagicMock(return_value=response))

    with pytest.raises(httpx.HTTPStatusError):
        cloud_run.run_job_execution("projects/p/locations/l/jobs/j", {})


def test_get_execution_fetches_the_execution_resource_url(monkeypatch):
    get = MagicMock(return_value=_fake_response({"succeededCount": 1}))
    monkeypatch.setattr(httpx, "get", get)

    result = cloud_run.get_execution("projects/p/locations/l/jobs/j/executions/e1")

    assert result == {"succeededCount": 1}
    url = get.call_args.args[0]
    assert url == "https://run.googleapis.com/v2/projects/p/locations/l/jobs/j/executions/e1"


def test_cancel_execution_posts_to_the_cancel_endpoint(monkeypatch):
    post = MagicMock(return_value=_fake_response({}))
    monkeypatch.setattr(httpx, "post", post)

    cloud_run.cancel_execution("projects/p/locations/l/jobs/j/executions/e1")

    url = post.call_args.args[0]
    assert url == "https://run.googleapis.com/v2/projects/p/locations/l/jobs/j/executions/e1:cancel"


# --- execution_is_terminal / execution_succeeded -----------------------------
# Payload shapes modeled after the Cloud Run Execution resource's
# succeededCount/failedCount/completionTime/conditions fields.

_STILL_RUNNING = {
    "name": "projects/p/locations/l/jobs/j/executions/e1",
    "runningCount": 1,
    "succeededCount": 0,
    "failedCount": 0,
    "conditions": [{"type": "Started", "state": "CONDITION_SUCCEEDED"}],
}

_SUCCEEDED = {
    "name": "projects/p/locations/l/jobs/j/executions/e1",
    "runningCount": 0,
    "succeededCount": 1,
    "failedCount": 0,
    "completionTime": "2026-01-01T00:10:00Z",
    "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
}

_FAILED = {
    "name": "projects/p/locations/l/jobs/j/executions/e1",
    "runningCount": 0,
    "succeededCount": 0,
    "failedCount": 1,
    "completionTime": "2026-01-01T00:10:00Z",
    "conditions": [{"type": "Completed", "state": "CONDITION_FAILED"}],
}


def test_execution_is_terminal_is_false_while_still_running():
    assert cloud_run.execution_is_terminal(_STILL_RUNNING) is False


def test_execution_is_terminal_is_true_once_succeeded():
    assert cloud_run.execution_is_terminal(_SUCCEEDED) is True


def test_execution_is_terminal_is_true_once_failed():
    assert cloud_run.execution_is_terminal(_FAILED) is True


def test_execution_is_terminal_is_true_from_conditions_alone_without_completion_time():
    no_completion_time = {k: v for k, v in _SUCCEEDED.items() if k != "completionTime"}
    assert cloud_run.execution_is_terminal(no_completion_time) is True


def test_execution_succeeded_is_true_only_when_succeeded_count_positive_and_no_failures():
    assert cloud_run.execution_succeeded(_SUCCEEDED) is True


def test_execution_succeeded_is_false_when_failed_count_is_positive():
    assert cloud_run.execution_succeeded(_FAILED) is False


def test_execution_succeeded_is_false_while_still_running():
    assert cloud_run.execution_succeeded(_STILL_RUNNING) is False
