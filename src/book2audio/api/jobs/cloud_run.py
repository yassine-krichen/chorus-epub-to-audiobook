"""Cloud Run Admin API v2 client for triggering/cancelling worker executions.

Uses httpx + google-auth directly instead of the google-cloud-run SDK, per
the approved architecture (one fewer heavy dependency for a handful of REST
calls). Application Default Credentials are used to obtain a bearer token;
in Render/Cloud Run this resolves to whatever service account credentials
are configured in the environment (e.g. GOOGLE_APPLICATION_CREDENTIALS).

NOTE: the exact Cloud Run Admin API v2 request/response field names below
(``overrides.containerOverrides[].env``, execution ``conditions``, etc.)
are reproduced from general familiarity with the API and were NOT verified
against live Google documentation in this session (no web-search tool was
available). Before relying on this in production, diff it against the
current https://cloud.google.com/run/docs/reference/rest/v2/ reference.
"""

from __future__ import annotations

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.auth import default as google_auth_default

_API_BASE = "https://run.googleapis.com/v2"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _access_token() -> str:
    credentials, _ = google_auth_default(scopes=_SCOPES)
    credentials.refresh(GoogleAuthRequest())
    return credentials.token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def run_job_execution(job_name: str, env_overrides: dict[str, str]) -> dict:
    """Trigger a new execution of ``job_name`` (e.g. ``projects/P/locations/L/jobs/J``).

    Returns the long-running Operation resource; the execution name can be
    read from ``operation["metadata"]["name"]`` once available.
    """
    url = f"{_API_BASE}/{job_name}:run"
    body = {
        "overrides": {
            "containerOverrides": [
                {"env": [{"name": k, "value": v} for k, v in env_overrides.items()]}
            ]
        }
    }
    response = httpx.post(url, json=body, headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def get_execution(execution_name: str) -> dict:
    """Fetch the current state of an execution (e.g. for reconciliation)."""
    url = f"{_API_BASE}/{execution_name}"
    response = httpx.get(url, headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def cancel_execution(execution_name: str) -> dict:
    url = f"{_API_BASE}/{execution_name}:cancel"
    response = httpx.post(url, json={}, headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def execution_is_terminal(execution: dict) -> bool:
    """True once the execution has stopped running, per its ``conditions``."""
    completion_time = execution.get("completionTime")
    if completion_time:
        return True
    for condition in execution.get("conditions", []):
        if condition.get("type") == "Completed" and condition.get("state") in (
            "CONDITION_SUCCEEDED",
            "CONDITION_FAILED",
        ):
            return True
    return False


def execution_succeeded(execution: dict) -> bool:
    return int(execution.get("succeededCount", 0)) > 0 and int(
        execution.get("failedCount", 0)
    ) == 0
