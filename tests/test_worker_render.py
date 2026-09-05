"""Tests for book2audio.worker.render -- the shared render orchestration used
by both worker.entrypoint (Cloud Run Job) and worker.local_daemon (hybrid
mode). Pipeline/parse_epub/download_file are all mocked -- no real EPUB
parsing, TTS synthesis, or R2 network call is exercised here (that's
test_pipeline_batches.py/test_pipeline_callbacks.py's and
test_r2_storage.py's job respectively).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from book2audio.config import Config
from book2audio.db.models import Job, User
from book2audio.models import BookMetadata, Chapter
from book2audio.worker import render as render_module


def _make_user(db_session) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_job(db_session, **overrides) -> Job:
    user = _make_user(db_session)
    defaults = dict(
        user_id=user.id,
        title="Book",
        epub_object_key="uploads/book.epub",
        config={},
        status="running",
        chapters=[],
        parts=[],
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


class _FakePipeline:
    """Stands in for Pipeline: records the callbacks it was built with and,
    on run(), fires on_chapter_done once -- enough to prove render_job wires
    (and optionally wraps) the hook correctly, without a real render."""

    def __init__(self, config, console, on_chapter_done=None, on_part_ready=None):
        self.config = config
        self.console = console
        self.on_chapter_done = on_chapter_done
        self.on_part_ready = on_part_ready

    def run(self) -> None:
        if self.on_chapter_done is not None:
            self.on_chapter_done(Chapter(index=1, title="Chapter One"))


# --- _download_inputs --------------------------------------------------


def test_download_inputs_downloads_the_epub_into_the_scratch_dir(monkeypatch, tmp_path):
    download_file = MagicMock()
    monkeypatch.setattr(render_module, "download_file", download_file)

    epub_path = render_module._download_inputs("uploads/x.epub", "jobs/1", str(tmp_path))

    expected_path = str(tmp_path / "book.epub")
    assert epub_path == expected_path
    download_file.assert_any_call("uploads/x.epub", expected_path)


def test_download_inputs_best_effort_downloads_a_prior_resume_state(monkeypatch, tmp_path):
    download_file = MagicMock()
    monkeypatch.setattr(render_module, "download_file", download_file)

    render_module._download_inputs("uploads/x.epub", "jobs/1", str(tmp_path))

    download_file.assert_any_call("jobs/1/state.json", str(tmp_path / "state.json"))


def test_download_inputs_ignores_a_missing_resume_state_and_still_returns_the_epub_path(
    monkeypatch, tmp_path
):
    def _side_effect(key, _path):
        if key.endswith("state.json"):
            raise FileNotFoundError("no such key")

    monkeypatch.setattr(render_module, "download_file", MagicMock(side_effect=_side_effect))

    epub_path = render_module._download_inputs("uploads/x.epub", "jobs/1", str(tmp_path))

    assert epub_path == str(tmp_path / "book.epub")


# --- _set_total_chapters -------------------------------------------------


def test_set_total_chapters_records_the_full_chapter_count_and_bumps_the_heartbeat(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), [1, 2, 3])),
    )
    config = Config(
        epub_path="book.epub", output_path=str(tmp_path / "book.m4b"), work_dir=str(tmp_path)
    )

    title = render_module._set_total_chapters(job.id, config)

    assert title == "My Book"
    db_session.refresh(job)
    assert job.total_chapters == 3
    assert job.last_heartbeat_at is not None


def test_set_total_chapters_caps_the_count_at_the_configured_chapter_limit(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), [1, 2, 3, 4, 5])),
    )
    config = Config(
        epub_path="book.epub",
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        chapter_limit=2,
    )

    render_module._set_total_chapters(job.id, config)

    db_session.refresh(job)
    assert job.total_chapters == 2


def test_set_total_chapters_matches_selection_bounds_for_a_start_chapter_and_chapter_limit_combo(
    db_session, monkeypatch, tmp_path
):
    """Regression guard: _set_total_chapters must derive its count from
    Config.selection_bounds rather than recomputing start_chapter/chapter_limit
    arithmetic itself -- these two have drifted apart once before."""
    job = _make_job(db_session)
    parsed = list(range(20))  # a 20-chapter book; contents are irrelevant here
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), parsed)),
    )
    config = Config(
        epub_path="book.epub",
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        start_chapter=10,
        chapter_limit=5,
    )
    start_pos, end_pos = config.selection_bounds(len(parsed))

    render_module._set_total_chapters(job.id, config)

    db_session.refresh(job)
    assert job.total_chapters == end_pos - start_pos


def test_set_total_chapters_does_not_raise_when_the_job_row_no_longer_exists(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), [1, 2])),
    )
    config = Config(
        epub_path="book.epub", output_path=str(tmp_path / "book.m4b"), work_dir=str(tmp_path)
    )

    title = render_module._set_total_chapters(uuid.uuid4(), config)

    assert title == "My Book"


# --- render_job: after_chapter wiring ------------------------------------


def _mock_common_deps(monkeypatch, base_on_chapter_done=None):
    monkeypatch.setattr(render_module, "download_file", MagicMock())
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), [1])),
    )
    monkeypatch.setattr(
        render_module,
        "build_progress_hooks",
        lambda *a, **kw: (base_on_chapter_done or (lambda ch: None), lambda *a: None),
    )
    monkeypatch.setattr(render_module, "Pipeline", _FakePipeline)


def test_render_job_wraps_after_chapter_around_the_base_progress_hook(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    base_calls = []
    _mock_common_deps(monkeypatch, base_on_chapter_done=lambda ch: base_calls.append(ch.index))
    after_calls = []

    render_module.render_job(
        job.id,
        job.epub_object_key,
        {},
        Console(),
        str(tmp_path),
        after_chapter=lambda ch: after_calls.append(ch.index),
    )

    assert base_calls == [1]
    assert after_calls == [1]


def test_render_job_calls_the_base_hook_unwrapped_when_after_chapter_is_none(
    db_session, monkeypatch, tmp_path
):
    """Cloud Run path (worker.entrypoint) doesn't pass after_chapter -- must
    behave exactly as before the render.py refactor."""
    job = _make_job(db_session)
    base_calls = []
    _mock_common_deps(monkeypatch, base_on_chapter_done=lambda ch: base_calls.append(ch.index))

    render_module.render_job(job.id, job.epub_object_key, {}, Console(), str(tmp_path))

    assert base_calls == [1]


def test_render_job_requires_freellmapi_configured_when_thumbnails_are_enabled(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    monkeypatch.setattr(render_module, "download_file", MagicMock())
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="My Book", author="A"), [1])),
    )
    require_configured = MagicMock(side_effect=RuntimeError("FREELLMAPI_API_KEY not set"))
    monkeypatch.setattr(render_module, "require_freellmapi_configured", require_configured)

    with pytest.raises(RuntimeError, match="FREELLMAPI_API_KEY"):
        render_module.render_job(
            job.id, job.epub_object_key, {"thumbnails": True}, Console(), str(tmp_path)
        )

    require_configured.assert_called_once()


def test_render_job_does_not_check_freellmapi_config_when_thumbnails_are_disabled(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    require_configured = MagicMock()
    monkeypatch.setattr(render_module, "require_freellmapi_configured", require_configured)
    _mock_common_deps(monkeypatch)

    render_module.render_job(job.id, job.epub_object_key, {}, Console(), str(tmp_path))

    require_configured.assert_not_called()
