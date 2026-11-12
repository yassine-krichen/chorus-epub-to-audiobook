"""Worker side of the cast review: parking a job after casting, and not finishing it."""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from book2audio.db.models import Job, User
from book2audio.models import BookMetadata
from book2audio.worker import entrypoint as entrypoint_module
from book2audio.worker import local_daemon as local_daemon_module
from book2audio.worker import progress as progress_module
from book2audio.worker import render as render_module
from book2audio.worker.local_daemon import _ClaimedJob
from book2audio.worker.progress import CastReviewRequested, build_cast_review_pause


def _make_job(db_session, **overrides) -> Job:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="hash")
    db_session.add(user)
    db_session.commit()
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


def _report(tmp_path, characters) -> str:
    path = tmp_path / "cast_report.json"
    path.write_text(json.dumps({"voice_mode": "cast", "characters": characters}), encoding="utf-8")
    return str(path)


# --- build_cast_review_pause ----------------------------------------------------------


def test_pausing_parks_the_job_and_stops_the_pipeline(db_session, monkeypatch, tmp_path):
    job = _make_job(db_session)
    monkeypatch.setattr(progress_module, "upload_file", MagicMock())
    monkeypatch.setattr(progress_module, "upload_bytes", MagicMock())
    pause = build_cast_review_pause(job.id, str(tmp_path), f"jobs/{job.id}")

    with pytest.raises(CastReviewRequested):
        pause(_report(tmp_path, [{"name": "Sunny"}]))

    db_session.refresh(job)
    assert job.status == "awaiting_cast_review"
    assert job.cast_review_requested_at is not None


def test_everything_the_resume_needs_is_uploaded_before_the_status_changes(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session)
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "attribution-cache.json").write_text("{}", encoding="utf-8")
    seen_status_at_upload: list[str] = []

    def upload(path: str, key: str) -> None:
        db_session.refresh(job)
        seen_status_at_upload.append(job.status)
        uploaded.append(key)

    uploaded: list[str] = []
    monkeypatch.setattr(progress_module, "upload_file", upload)
    monkeypatch.setattr(
        progress_module, "upload_bytes", lambda data, key, content_type=None: upload(None, key)
    )
    pause = build_cast_review_pause(job.id, str(tmp_path), f"jobs/{job.id}")

    with pytest.raises(CastReviewRequested):
        pause(_report(tmp_path, [{"name": "Sunny"}]))

    assert f"jobs/{job.id}/cast_report.json" in uploaded
    assert f"jobs/{job.id}/state.json" in uploaded
    assert f"jobs/{job.id}/attribution-cache.json" in uploaded
    assert set(seen_status_at_upload) == {"running"}


def test_a_failed_upload_fails_the_job_instead_of_parking_it(db_session, monkeypatch, tmp_path):
    job = _make_job(db_session)
    monkeypatch.setattr(progress_module, "upload_file", MagicMock(side_effect=OSError("r2 down")))
    monkeypatch.setattr(progress_module, "upload_bytes", MagicMock(side_effect=OSError("r2 down")))
    pause = build_cast_review_pause(job.id, str(tmp_path), f"jobs/{job.id}")

    with pytest.raises(OSError):
        pause(_report(tmp_path, [{"name": "Sunny"}]))

    db_session.refresh(job)
    assert job.status == "running"  # the entrypoint then marks it failed; it never waits unresumable


def test_a_job_canceled_meanwhile_keeps_its_status_but_still_stops(
    db_session, monkeypatch, tmp_path
):
    job = _make_job(db_session, status="canceled")
    monkeypatch.setattr(progress_module, "upload_file", MagicMock())
    monkeypatch.setattr(progress_module, "upload_bytes", MagicMock())
    pause = build_cast_review_pause(job.id, str(tmp_path), f"jobs/{job.id}")

    with pytest.raises(CastReviewRequested):
        pause(_report(tmp_path, [{"name": "Sunny"}]))

    db_session.refresh(job)
    assert job.status == "canceled"


# --- render_job -------------------------------------------------------------------------


class _ReportingPipeline:
    """Calls the cast report hook like the real pipeline, then would render."""

    rendered = False

    def __init__(self, config, console, on_cast_report=None, **_):
        self.on_cast_report = on_cast_report
        self.report_path = _ReportingPipeline.report_path

    def run(self) -> None:
        self.on_cast_report(self.report_path)
        _ReportingPipeline.rendered = True


@pytest.fixture
def render_env(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(render_module, "download_file", MagicMock())
    monkeypatch.setattr(
        render_module,
        "parse_epub",
        MagicMock(return_value=(BookMetadata(title="Book", author="A"), [])),
    )
    monkeypatch.setattr(render_module, "build_progress_hooks", lambda *a, **k: (MagicMock(), MagicMock()))
    monkeypatch.setattr(render_module, "Pipeline", _ReportingPipeline)
    monkeypatch.setattr(progress_module, "upload_file", MagicMock())
    monkeypatch.setattr(progress_module, "upload_bytes", MagicMock())
    _ReportingPipeline.rendered = False
    _ReportingPipeline.report_path = _report(tmp_path, [{"name": "Sunny"}])
    return _make_job(db_session)


def _run(job, tmp_path, config):
    return render_module.render_job(
        job.id, job.epub_object_key, config, Console(), str(tmp_path)
    )


def test_a_job_with_review_on_stops_after_casting_and_reports_it(render_env, tmp_path):
    outcome = _run(render_env, tmp_path, {"cast_review": True})

    assert outcome == "awaiting_cast_review"
    assert _ReportingPipeline.rendered is False


def test_a_job_with_review_off_renders_straight_through(render_env, tmp_path):
    outcome = _run(render_env, tmp_path, {"cast_review": False})

    assert outcome == "finished"
    assert _ReportingPipeline.rendered is True


def test_a_narrator_only_job_never_pauses_even_with_review_on(render_env, tmp_path):
    outcome = _run(render_env, tmp_path, {"cast_review": True, "voice_mode": "narrator_only"})

    assert outcome == "finished"


def test_a_cast_with_nobody_to_review_renders_without_pausing(render_env, tmp_path):
    _ReportingPipeline.report_path = _report(tmp_path, [])

    outcome = _run(render_env, tmp_path, {"cast_review": True})

    assert outcome == "finished"


def test_an_unreadable_cast_report_renders_instead_of_stalling(render_env, tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("not json", encoding="utf-8")
    _ReportingPipeline.report_path = str(bad)

    outcome = _run(render_env, tmp_path, {"cast_review": True})

    assert outcome == "finished"


def test_the_resumed_run_after_confirmation_renders(render_env, tmp_path):
    # The API turns cast_review off when the user confirms the cast.
    config = {"cast_review": False, "character_aliases": {"Rain": "Sunny"}}

    assert _run(render_env, tmp_path, config) == "finished"


# --- the two entrypoints leave a parked job alone -------------------------------------


def test_the_cloud_entrypoint_does_not_mark_a_parked_job_succeeded(db_session, monkeypatch, tmp_path):
    job = _make_job(db_session, status="awaiting_cast_review")
    monkeypatch.setenv("JOB_ID", str(job.id))
    monkeypatch.setenv("EPUB_OBJECT_KEY", "uploads/book.epub")
    monkeypatch.setattr(entrypoint_module, "_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(entrypoint_module, "render_job", MagicMock(return_value="awaiting_cast_review"))

    exit_code = entrypoint_module.main()

    db_session.refresh(job)
    assert exit_code == 0
    assert job.status == "awaiting_cast_review"
    assert job.finished_at is None


def test_the_cloud_entrypoint_still_marks_a_finished_job_succeeded(db_session, monkeypatch, tmp_path):
    job = _make_job(db_session)
    monkeypatch.setenv("JOB_ID", str(job.id))
    monkeypatch.setenv("EPUB_OBJECT_KEY", "uploads/book.epub")
    monkeypatch.setattr(entrypoint_module, "_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr(entrypoint_module, "render_job", MagicMock(return_value="finished"))

    entrypoint_module.main()

    db_session.refresh(job)
    assert job.status == "succeeded"


def test_the_local_daemon_does_not_mark_a_parked_job_succeeded(db_session, monkeypatch):
    job = _make_job(db_session, status="awaiting_cast_review")
    claimed = _ClaimedJob(id=job.id, epub_object_key="uploads/book.epub", config={})
    monkeypatch.setattr(
        local_daemon_module, "render_job", MagicMock(return_value="awaiting_cast_review")
    )

    local_daemon_module._process_job(claimed)

    db_session.refresh(job)
    assert job.status == "awaiting_cast_review"
    assert job.finished_at is None


def test_the_local_daemon_cleans_up_the_scratch_dir_of_a_parked_job(db_session, monkeypatch):
    job = _make_job(db_session, status="awaiting_cast_review")
    claimed = _ClaimedJob(id=job.id, epub_object_key="uploads/book.epub", config={})
    seen = {}

    def fake_render_job(job_id, key, config, console, scratch_dir, **kwargs):
        seen["path"] = scratch_dir
        return "awaiting_cast_review"

    monkeypatch.setattr(local_daemon_module, "render_job", fake_render_job)

    local_daemon_module._process_job(claimed)

    # The resumed run restores its files from storage, so nothing is kept locally.
    assert not os.path.exists(seen["path"])
