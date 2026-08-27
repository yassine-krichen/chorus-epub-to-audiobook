"""Regression tests for Pipeline's optional on_chapter_done/on_part_ready hooks.

Exercises Pipeline._render_and_assemble directly (like test_pipeline_batches.py
does for its siblings) with a fake TTS engine and a stubbed build_m4b, so these
run fast and don't need ffmpeg or a real Kokoro model.
"""

from __future__ import annotations

import os

import numpy as np

import book2audio.pipeline as pipeline_module
from book2audio.config import Config
from book2audio.models import BookMetadata, Chapter, Segment, SegmentKind
from book2audio.pipeline import Pipeline
from book2audio.state import RenderState
from book2audio.tts.voices import VoiceCast


class _FakeEngine:
    def synthesize(self, text, voice, speed):
        return np.zeros(200, dtype=np.float32)


def _fake_build_m4b(chapters, metadata, out_path, work_dir, bitrate="64k"):
    open(out_path, "w").close()
    return out_path


def _chapters(n):
    return [
        Chapter(
            index=i,
            title=f"Chapter {i}",
            segments=[Segment(kind=SegmentKind.NARRATION, text="Hello there.", speaker="Narrator")],
        )
        for i in range(1, n + 1)
    ]


def _pipeline(tmp_path, batch_size=2, **callbacks):
    cfg = Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        music=False,
        batch_size=batch_size,
    )
    return cfg, Pipeline(cfg, **callbacks)


def _fresh_state(tmp_path):
    return RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="Book", narrator_voice="am_michael"
    )


def test_on_chapter_done_fires_once_per_freshly_rendered_chapter(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    chapter_calls = []
    cfg, p = _pipeline(tmp_path, on_chapter_done=lambda ch: chapter_calls.append(ch.index))
    os.makedirs(os.path.join(cfg.work_dir, "chapters"), exist_ok=True)

    p._render_and_assemble(
        _chapters(4),
        BookMetadata(title="Book", author="Author"),
        _FakeEngine(),
        VoiceCast(),
        _fresh_state(tmp_path),
        os.path.join(cfg.work_dir, "chapters"),
    )

    assert chapter_calls == [1, 2, 3, 4]


def test_on_part_ready_fires_once_per_completed_batch_with_the_right_args(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    part_calls = []
    cfg, p = _pipeline(
        tmp_path,
        batch_size=2,
        on_part_ready=lambda part_no, out_path, batch: part_calls.append(
            (part_no, out_path, [c.index for c in batch])
        ),
    )
    os.makedirs(os.path.join(cfg.work_dir, "chapters"), exist_ok=True)

    p._render_and_assemble(
        _chapters(4),
        BookMetadata(title="Book", author="Author"),
        _FakeEngine(),
        VoiceCast(),
        _fresh_state(tmp_path),
        os.path.join(cfg.work_dir, "chapters"),
    )

    assert [call[0] for call in part_calls] == [1, 2]
    assert [call[2] for call in part_calls] == [[1, 2], [3, 4]]
    assert part_calls[0][1].endswith("book-part-01.m4b")


def test_neither_hook_fires_again_for_chapters_already_rendered_in_a_prior_run(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    chapters_dir = os.path.join(str(tmp_path), "chapters")
    os.makedirs(chapters_dir, exist_ok=True)
    metadata = BookMetadata(title="Book", author="Author")

    cfg, first_run = _pipeline(tmp_path, batch_size=2)
    first_run._render_and_assemble(
        _chapters(4), metadata, _FakeEngine(), VoiceCast(), _fresh_state(tmp_path), chapters_dir
    )

    chapter_calls = []
    part_calls = []
    _, second_run = _pipeline(
        tmp_path,
        batch_size=2,
        on_chapter_done=lambda ch: chapter_calls.append(ch.index),
        on_part_ready=lambda *a: part_calls.append(a),
    )
    resumed_state = RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="Book", narrator_voice="am_michael"
    )
    second_run._render_and_assemble(
        _chapters(4), metadata, _FakeEngine(), VoiceCast(), resumed_state, chapters_dir
    )

    assert chapter_calls == []
    assert part_calls == []


def test_on_chapter_done_is_not_required_and_defaults_to_none(tmp_path, monkeypatch):
    """Pipeline must work when neither callback is supplied (CLI usage)."""
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    cfg, p = _pipeline(tmp_path, batch_size=2)
    os.makedirs(os.path.join(cfg.work_dir, "chapters"), exist_ok=True)

    outputs = p._render_and_assemble(
        _chapters(2),
        BookMetadata(title="Book", author="Author"),
        _FakeEngine(),
        VoiceCast(),
        _fresh_state(tmp_path),
        os.path.join(cfg.work_dir, "chapters"),
    )

    assert len(outputs) == 1
