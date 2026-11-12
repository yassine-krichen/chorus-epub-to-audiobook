"""Pipeline.on_cast_report and the hosted worker's cast-report upload."""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest

import book2audio.pipeline as pipeline_module
from book2audio.config import Config
from book2audio.epub import ParsedChapter
from book2audio.models import BookMetadata
from book2audio.pipeline import Pipeline
from book2audio.worker import progress as progress_module

_BOOK = BookMetadata(title="Book", author="Author")


def _config(tmp_path) -> Config:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"not read: parse_epub is stubbed")
    return Config(
        epub_path=str(epub),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        music=False,
    )


@pytest.fixture
def stubbed_pipeline(monkeypatch):
    chapters = [
        ParsedChapter(index=1, title="Chapter 1", paragraphs=['"Hello," said Sunny.'])
    ]
    monkeypatch.setattr(pipeline_module, "parse_epub", lambda path, cover_dir: (_BOOK, chapters))


def test_the_cast_report_path_is_handed_to_the_hook_before_the_tts_model_loads(
    tmp_path, stubbed_pipeline, monkeypatch
):
    events: list[str] = []

    def record_report(path: str) -> None:
        events.append("report")
        with open(path, encoding="utf-8") as report_file:
            assert json.load(report_file)["voice_mode"] == "cast"

    def load_engine(self, metadata):
        events.append("engine")
        return object()

    monkeypatch.setattr(Pipeline, "_load_engine", load_engine)
    monkeypatch.setattr(Pipeline, "_render_and_assemble", lambda self, *a, **kw: [])
    pipeline = Pipeline(_config(tmp_path), on_cast_report=record_report)

    pipeline.run()

    assert events == ["report", "engine"]


def test_the_pipeline_runs_without_a_cast_report_hook(tmp_path, stubbed_pipeline, monkeypatch):
    monkeypatch.setattr(Pipeline, "_load_engine", lambda self, metadata: object())
    monkeypatch.setattr(Pipeline, "_render_and_assemble", lambda self, *a, **kw: [])

    Pipeline(_config(tmp_path)).run()  # must not raise


def _report_file(tmp_path) -> str:
    path = tmp_path / "cast_report.json"
    path.write_text(json.dumps({"voice_mode": "cast", "characters": []}), encoding="utf-8")
    return str(path)


def test_the_uploader_stores_the_report_under_the_job_prefix(monkeypatch, tmp_path):
    upload = MagicMock()
    monkeypatch.setattr(progress_module, "upload_bytes", upload)
    job_id = uuid.uuid4()

    progress_module.build_cast_report_uploader(job_id)(_report_file(tmp_path))

    data, key = upload.call_args.args
    assert key == f"jobs/{job_id}/cast_report.json"
    assert json.loads(data)["voice_mode"] == "cast"
    assert upload.call_args.kwargs == {"content_type": "application/json"}


def test_a_failed_report_upload_is_logged_and_does_not_stop_the_render(
    monkeypatch, caplog, tmp_path
):
    monkeypatch.setattr(progress_module, "upload_bytes", MagicMock(side_effect=OSError("r2 down")))

    with caplog.at_level(logging.WARNING):
        progress_module.build_cast_report_uploader(uuid.uuid4())(_report_file(tmp_path))

    assert "could not upload the cast report" in caplog.text
