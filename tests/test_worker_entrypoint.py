"""Tests for the worker entrypoint's config-building logic
(book2audio.worker.entrypoint._build_config).

Regression coverage for JobConfig gaining a ``thumbnails`` field: before that
field existed on the wire contract, ``config.thumbnails`` was always False
for every job created through the API, regardless of what a client sent.
"""

from __future__ import annotations

from book2audio.config import Config
from book2audio.worker.entrypoint import _build_config


def test_build_config_defaults_thumbnails_to_false_when_absent():
    config = _build_config("book.epub", "/tmp/scratch", {})

    assert isinstance(config, Config)
    assert config.thumbnails is False


def test_build_config_propagates_thumbnails_true_from_the_persisted_job_config():
    config = _build_config("book.epub", "/tmp/scratch", {"thumbnails": True})

    assert config.thumbnails is True
