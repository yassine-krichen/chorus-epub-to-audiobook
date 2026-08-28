"""Tests for the FastAPI app factory's startup fail-fast checks
(book2audio.api.main.create_app).

Only the WORKER_MODE validation added alongside hybrid-mode is covered here;
the pre-existing CORS_ORIGINS wildcard check is untouched by this change and
already implicitly exercised by every test using the ``client`` fixture with
CORS_ORIGINS="" -- out of scope for this file.
"""

from __future__ import annotations

import pytest

from book2audio.api.main import create_app


def test_create_app_raises_when_worker_mode_is_invalid(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "not-a-real-mode")

    with pytest.raises(RuntimeError, match="WORKER_MODE"):
        create_app()


def test_create_app_succeeds_with_worker_mode_unset(monkeypatch):
    monkeypatch.delenv("WORKER_MODE", raising=False)

    create_app()  # must not raise


def test_create_app_succeeds_with_worker_mode_set_to_local(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "local")

    create_app()  # must not raise
