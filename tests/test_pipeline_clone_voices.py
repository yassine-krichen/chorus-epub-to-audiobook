"""The pipeline with cloned voices: routing, Kokoro fallback, resume, report, GPU release.

A fake clone engine and a fake Kokoro stand in for the real ones, so nothing needs a GPU,
ffmpeg or model files.
"""

from __future__ import annotations

import json
import os
import uuid

import numpy as np
import pytest

import book2audio.pipeline as pipeline_module
from book2audio.config import Config
from book2audio.models import Book, BookMetadata, Chapter, Segment, SegmentKind
from book2audio.pipeline import Pipeline
from book2audio.state import RenderState
from book2audio.api.jobs.schemas import CastReport
from book2audio.cast_report import read_review_report, record_clone_fallbacks
from book2audio.tts.clone_engine import (
    FALLBACK_REASONS,
    REASON_NOT_INSTALLED,
    REASON_OOM,
    REASON_STOPPED,
    REASON_VOICE_GONE,
    CloneEngineError,
    CloneReference,
    RoutingEngine,
)
from book2audio.tts.voices import VoiceCast, clone_voice_id

CLONE_VOICE = clone_voice_id(uuid.uuid4())
# What a real engine failure can look like: paths, a secret, and a very long message.
DETAIL = "oom: CUDA out of memory C:/Users/someone/venv/torch/cuda.py token=sk-123 " + "x" * 5000


class FakeKokoro:
    def __init__(self) -> None:
        self.voices: list[str] = []

    def synthesize(self, text, voice, speed=1.0):
        self.voices.append(voice)
        return np.full(100, 0.1, dtype=np.float32)


class FakeClone:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.closed = 0

    def synthesize_clone(self, text, reference):
        self.calls += 1
        if self.fail:
            raise CloneEngineError(DETAIL, reason=REASON_OOM)
        return np.full(300, 0.2, dtype=np.float32)

    def close(self):
        self.closed += 1


def _reference(tmp_path, gender="f") -> CloneReference:
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFF")
    return CloneReference(CLONE_VOICE, str(wav), "text", "a" * 64, gender)


def _chapters(n=2):
    return [
        Chapter(
            index=i,
            title=f"Chapter {i}",
            segments=[Segment(kind=SegmentKind.NARRATION, text="Hello there.", speaker="Narrator")],
        )
        for i in range(1, n + 1)
    ]


def _fake_build_m4b(chapters, metadata, out_path, work_dir, bitrate="64k"):
    open(out_path, "w").close()
    return out_path


def _config(tmp_path, **overrides) -> Config:
    (tmp_path / "book.epub").write_bytes(b"x")
    return Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        music=False,
        batch_size=0,
        narrator_voice=CLONE_VOICE,
        **overrides,
    )


def _state(tmp_path) -> RenderState:
    return RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="Book", narrator_voice=CLONE_VOICE
    )


def _render(tmp_path, pipeline, kokoro, chapters=None):
    cfg = pipeline.config
    os.makedirs(os.path.join(cfg.work_dir, "chapters"), exist_ok=True)
    cast = VoiceCast(narrator_voice=CLONE_VOICE)
    engine = pipeline._route_clone_voices(kokoro, cast)
    pipeline._render_and_assemble(
        chapters or _chapters(),
        BookMetadata(title="Book", author="A"),
        engine,
        cast,
        _state(tmp_path),
        os.path.join(cfg.work_dir, "chapters"),
    )
    return cast


@pytest.fixture(autouse=True)
def _no_ffmpeg(monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)


def test_a_job_without_cloned_voices_uses_the_plain_engine(tmp_path):
    cfg = _config(tmp_path)
    cfg.narrator_voice = "am_michael"
    pipeline = Pipeline(cfg)
    kokoro = FakeKokoro()

    engine = pipeline._route_clone_voices(kokoro, VoiceCast(narrator_voice="am_michael"))

    assert engine is kokoro and pipeline._router is None


def test_a_cloned_narrator_is_spoken_by_the_clone_engine_not_kokoro(tmp_path):
    clone, kokoro = FakeClone(), FakeKokoro()
    pipeline = Pipeline(
        _config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)}
    )
    pipeline._clone_engine = clone

    _render(tmp_path, pipeline, kokoro)

    assert clone.calls >= 2  # a heading and a paragraph per chapter
    assert kokoro.voices == []
    assert pipeline.clone_fallbacks == []


def test_when_cloning_fails_the_chapter_still_renders_with_a_kokoro_voice(tmp_path):
    clone, kokoro = FakeClone(fail=True), FakeKokoro()
    pipeline = Pipeline(
        _config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path, gender="f")}
    )
    pipeline._clone_engine = clone

    _render(tmp_path, pipeline, kokoro)

    wavs = sorted(os.listdir(tmp_path / "chapters"))
    assert wavs == ["chapter_0001.wav", "chapter_0002.wav"]
    assert kokoro.voices and all(v.startswith("af_") for v in kokoro.voices)
    assert clone.calls == 1  # not tried again after the first failure
    [fallback] = pipeline.clone_fallbacks
    assert fallback["voice"] == CLONE_VOICE and fallback["reason"] == REASON_OOM


def test_a_chapter_read_by_the_fallback_is_rendered_again_once_cloning_works(tmp_path):
    reference = _reference(tmp_path)
    failing = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    failing._clone_engine = FakeClone(fail=True)
    _render(tmp_path, failing, FakeKokoro())

    working_clone = FakeClone()
    working = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    working._clone_engine = working_clone
    _render(tmp_path, working, FakeKokoro())

    assert working_clone.calls >= 2  # both chapters were done again, with the real voice


def test_chapters_read_by_the_clone_engine_are_kept_on_a_resume(tmp_path):
    reference = _reference(tmp_path)
    first = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    first._clone_engine = FakeClone()
    _render(tmp_path, first, FakeKokoro())

    second_clone = FakeClone()
    second = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    second._clone_engine = second_clone
    _render(tmp_path, second, FakeKokoro())

    assert second_clone.calls == 0


def test_the_hash_does_not_change_for_an_engineless_worker_that_always_falls_back(tmp_path):
    # Without an engine the fallback voice is known up front, so a re-run is not redone forever.
    reference = _reference(tmp_path)
    first = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    kokoro_one = FakeKokoro()
    _render(tmp_path, first, kokoro_one)
    second = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: reference})
    kokoro_two = FakeKokoro()

    _render(tmp_path, second, kokoro_two)

    assert kokoro_one.voices and kokoro_two.voices == []


def test_the_plan_says_which_cloned_voices_cannot_be_spoken_before_rendering(tmp_path):
    cast = VoiceCast(narrator_voice=CLONE_VOICE)
    missing = Pipeline(_config(tmp_path))
    engineless = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path, "f")})
    ready = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})
    ready._clone_engine = FakeClone()

    assert missing._planned_clone_fallbacks(cast)[0]["reason"] == REASON_VOICE_GONE
    [plan] = engineless._planned_clone_fallbacks(cast)
    assert plan["reason"] == REASON_NOT_INSTALLED and plan["fallback_voice"].startswith("af_")
    assert ready._planned_clone_fallbacks(cast) == []


def test_the_cast_report_records_planned_fallbacks(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path, "m")})
    cast = VoiceCast(narrator_voice=CLONE_VOICE)

    path = pipeline.report_cast([], cast)

    report = json.loads(open(path, encoding="utf-8").read())
    assert report["narrator_voice"] == CLONE_VOICE
    assert report["clone_fallbacks"][0]["voice"] == CLONE_VOICE
    assert report["clone_fallbacks"][0]["fallback_voice"].startswith("am_")


def test_a_failure_during_the_render_is_added_to_the_saved_report(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})
    pipeline._clone_engine = FakeClone(fail=True)
    cast = VoiceCast(narrator_voice=CLONE_VOICE)
    path = pipeline.report_cast([], cast)
    assert json.loads(open(path, encoding="utf-8").read())["clone_fallbacks"] == []

    _render(tmp_path, pipeline, FakeKokoro())
    pipeline._record_runtime_clone_fallbacks()

    saved = json.loads(open(path, encoding="utf-8").read())
    assert saved["clone_fallbacks"][0]["voice"] == CLONE_VOICE


def test_run_releases_the_clone_engine_when_the_job_ends_and_when_it_fails(tmp_path, monkeypatch):
    for failing in (False, True):
        clone = FakeClone()
        pipeline = Pipeline(
            _config(tmp_path),
            clone_voices={CLONE_VOICE: _reference(tmp_path)},
            clone_engine_factory=lambda clone=clone: clone,
        )
        book = Book(metadata=BookMetadata(title="Book", author="A"), chapters=_chapters(1))
        monkeypatch.setattr(Pipeline, "_load_and_prepare", lambda self, book=book: book)
        monkeypatch.setattr(Pipeline, "_load_engine", lambda self, metadata: FakeKokoro())
        if failing:
            monkeypatch.setattr(
                Pipeline, "_render_and_assemble", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
            )
            with pytest.raises(RuntimeError):
                pipeline.run()
        else:
            pipeline.run()

        assert clone.closed == 1, f"failing={failing}"
        monkeypatch.undo()
        monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)


def test_the_engine_is_released_even_when_the_cast_review_pauses_the_job(tmp_path, monkeypatch):
    clone = FakeClone()

    class Paused(Exception):
        pass

    def pause(path):
        raise Paused

    pipeline = Pipeline(
        _config(tmp_path),
        clone_voices={CLONE_VOICE: _reference(tmp_path)},
        clone_engine_factory=lambda: clone,
        on_cast_report=pause,
    )
    book = Book(metadata=BookMetadata(title="Book", author="A"), chapters=_chapters(1))
    monkeypatch.setattr(Pipeline, "_load_and_prepare", lambda self: book)

    with pytest.raises(Paused):
        pipeline.run()

    assert clone.closed == 1


def test_a_worker_with_no_factory_has_no_clone_engine(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})

    assert pipeline._start_clone_engine(VoiceCast(narrator_voice=CLONE_VOICE)) is None


def test_the_router_is_a_routing_engine_when_cloned_voices_are_in_use(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})

    engine = pipeline._route_clone_voices(FakeKokoro(), VoiceCast(narrator_voice=CLONE_VOICE))

    assert isinstance(engine, RoutingEngine)


# --- a failure's detail never reaches the report the website shows ----------------------------


def test_a_long_engine_failure_reaches_the_website_report_as_a_fixed_reason(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})
    pipeline._clone_engine = FakeClone(fail=True)
    path = pipeline.report_cast([], VoiceCast(narrator_voice=CLONE_VOICE))

    _render(tmp_path, pipeline, FakeKokoro())
    pipeline._record_runtime_clone_fallbacks()
    uploaded = read_review_report(path)  # exactly what the worker uploads for the job page

    report = CastReport.model_validate_json(uploaded)
    assert [f.reason for f in report.clone_fallbacks] == [REASON_OOM]
    assert b"someone" not in uploaded and b"sk-123" not in uploaded and b"xxxx" not in uploaded


def test_a_foreign_or_oversized_reason_is_replaced_and_the_report_stays_valid(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})
    path = pipeline.report_cast([], VoiceCast(narrator_voice=CLONE_VOICE))

    record_clone_fallbacks(
        path,
        [{"voice": CLONE_VOICE + "v" * 500, "reason": DETAIL, "fallback_voice": "am_michael" * 20}],
    )

    report = CastReport.model_validate_json(read_review_report(path))
    [note] = report.clone_fallbacks
    assert note.reason == REASON_STOPPED
    assert len(note.voice) <= 64 and len(note.fallback_voice) <= 40


def test_at_most_twenty_notes_are_kept(tmp_path):
    pipeline = Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)})
    path = pipeline.report_cast([], VoiceCast(narrator_voice=CLONE_VOICE))

    record_clone_fallbacks(
        path, [{"voice": CLONE_VOICE, "reason": REASON_OOM, "fallback_voice": "am_michael"}] * 50
    )

    assert len(CastReport.model_validate_json(read_review_report(path)).clone_fallbacks) == 20


def test_every_planned_fallback_reason_is_one_of_the_fixed_ones(tmp_path):
    cast = VoiceCast(narrator_voice=CLONE_VOICE)
    for pipeline in (
        Pipeline(_config(tmp_path)),
        Pipeline(_config(tmp_path), clone_voices={CLONE_VOICE: _reference(tmp_path)}),
    ):
        for note in pipeline._planned_clone_fallbacks(cast):
            assert note["reason"] in FALLBACK_REASONS
