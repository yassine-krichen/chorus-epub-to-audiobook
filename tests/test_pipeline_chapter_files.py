"""One output file per chapter, named after the chapter.

Exercises Pipeline._render_and_assemble with a fake TTS engine and a stubbed
build_m4b (same approach as test_pipeline_callbacks.py), so no ffmpeg or Kokoro.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

import book2audio.pipeline as pipeline_module
from book2audio.config import Config
from book2audio.models import BookMetadata, Chapter, Segment, SegmentKind
from book2audio.pipeline import Pipeline
from book2audio.state import RenderState
from book2audio.tts.voices import VoiceCast

_METADATA = BookMetadata(title="Shadow Slave", author="Guiltythree")


class _FakeEngine:
    def synthesize(self, text, voice, speed):
        return np.zeros(200, dtype=np.float32)


def _fake_build_m4b(chapters, metadata, out_path, work_dir, bitrate="64k"):
    open(out_path, "w").close()
    return out_path


def _chapters(*titles):
    # index is the parser position; number is the book's own chapter number.
    return [
        Chapter(
            index=position,
            title=title,
            number=position,
            segments=[Segment(kind=SegmentKind.NARRATION, text="Hello there.", speaker="Narrator")],
        )
        for position, title in enumerate(titles, start=1)
    ]


def _run(run_dir, chapters, *, batch_size=1, out_dir=None, **config):
    """Render ``chapters`` in ``run_dir``; returns the output paths."""
    out_dir = out_dir or run_dir
    os.makedirs(run_dir / "chapters", exist_ok=True)
    cfg = Config(
        epub_path=str(run_dir / "book.epub"),
        output_path=str(out_dir / "book.m4b"),
        work_dir=str(run_dir),
        music=False,
        batch_size=batch_size,
        **config,
    )
    state = RenderState.load_or_new(
        str(run_dir / "state.json"), source="book.epub", title="Shadow Slave", narrator_voice="x"
    )
    return Pipeline(cfg)._render_and_assemble(
        chapters, _METADATA, _FakeEngine(), VoiceCast(), state, str(run_dir / "chapters")
    )


def _names(paths):
    return [os.path.basename(p) for p in paths]


def test_one_file_per_chapter_is_named_by_number_and_title_without_the_chapter_prefix(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(
        tmp_path,
        _chapters("Chapter 1: Nightmare Begins", "Chapter 2: Hidden Spring"),
    )

    assert _names(outputs) == ["001 - Nightmare Begins.m4b", "002 - Hidden Spring.m4b"]
    assert all(os.path.exists(path) for path in outputs)


def test_one_file_per_chapter_uses_the_book_number_not_the_parser_position(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    chapters = _chapters("SYNOPSIS", "Chapter 1: Nightmare Begins")
    chapters[1].number = 1  # a synopsis sits at parser position 1

    outputs = _run(tmp_path, chapters[1:])

    assert _names(outputs) == ["001 - Nightmare Begins.m4b"]


def test_a_single_selected_chapter_is_still_named_after_the_chapter(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(tmp_path, _chapters("Chapter 1: Nightmare Begins"))

    assert _names(outputs) == ["001 - Nightmare Begins.m4b"]


def test_chapters_sharing_a_title_get_distinct_file_names(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    chapters = _chapters("Interlude", "interlude")
    for chapter in chapters:
        chapter.number = 5

    outputs = _run(tmp_path, chapters)

    assert _names(outputs) == ["005 - Interlude.m4b", "005 - interlude (2).m4b"]


def test_multi_chapter_parts_keep_the_legacy_names_without_a_template(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(tmp_path, _chapters("A", "B", "C", "D"), batch_size=2)

    assert _names(outputs) == ["book-part-01.m4b", "book-part-02.m4b"]


def test_a_single_multi_chapter_file_keeps_the_plain_output_path_without_a_template(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(tmp_path, _chapters("A", "B"), batch_size=0)

    assert _names(outputs) == ["book.m4b"]


def test_an_explicit_template_names_multi_chapter_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(
        tmp_path,
        _chapters("A", "B", "C", "D"),
        batch_size=2,
        filename_template="{title} - from chapter {chapter:02d}",
    )

    assert _names(outputs) == [
        "Shadow Slave - from chapter 01.m4b",
        "Shadow Slave - from chapter 03.m4b",
    ]


def test_resuming_the_same_run_reuses_its_files_instead_of_renaming_or_rebuilding(
    tmp_path, monkeypatch
):
    built = []

    def counting_build(chapters, metadata, out_path, work_dir, bitrate="64k"):
        built.append(out_path)
        return _fake_build_m4b(chapters, metadata, out_path, work_dir, bitrate)

    monkeypatch.setattr(pipeline_module, "build_m4b", counting_build)
    chapters = _chapters("Chapter 1: Nightmare Begins", "Chapter 2: Hidden Spring")

    first = _run(tmp_path, chapters)
    second = _run(tmp_path, chapters)

    assert second == first
    assert len(built) == 2  # nothing rebuilt on resume


def test_a_file_from_another_run_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)
    shared_out = tmp_path / "output"
    shared_out.mkdir()
    other_run_file = shared_out / "001 - Nightmare Begins.m4b"
    other_run_file.write_text("another book's audio")

    outputs = _run(
        tmp_path / "run-b",
        _chapters("Chapter 1: Nightmare Begins"),
        out_dir=shared_out,
    )

    assert _names(outputs) == ["001 - Nightmare Begins (2).m4b"]
    assert other_run_file.read_text() == "another book's audio"


def test_legacy_file_names_keep_part_names_even_with_one_chapter_per_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "build_m4b", _fake_build_m4b)

    outputs = _run(tmp_path, _chapters("Chapter 1: A", "Chapter 2: B"), legacy_file_names=True)

    assert _names(outputs) == ["book-part-01.m4b", "book-part-02.m4b"]


def test_a_state_file_written_before_part_files_existed_still_loads(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "source": "book.epub",
                "title": "Shadow Slave",
                "narrator_voice": "x",
                "completed": [1],
                "chapter_hashes": {"1": "abc"},
            }
        ),
        encoding="utf-8",
    )

    state = RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="t", narrator_voice="x"
    )

    assert state.completed == [1]
    assert state.part_files == {}


def test_a_changed_chapter_is_rebuilt_into_its_own_recorded_file_only(tmp_path, monkeypatch):
    built = []

    def counting_build(chapters, metadata, out_path, work_dir, bitrate="64k"):
        built.append(os.path.basename(out_path))
        return _fake_build_m4b(chapters, metadata, out_path, work_dir, bitrate)

    monkeypatch.setattr(pipeline_module, "build_m4b", counting_build)
    chapters = _chapters("Chapter 1: Nightmare Begins", "Chapter 2: Hidden Spring")
    first = _run(tmp_path, chapters)
    built.clear()

    chapters[0].segments[0].text = "A different opening line."
    second = _run(tmp_path, chapters)

    assert second == first
    assert built == ["001 - Nightmare Begins.m4b"]


def test_a_new_part_never_takes_a_name_recorded_for_a_later_part(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "X.m4b").write_text("another run")  # forces part 1 off "X.m4b"
    state = RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="t", narrator_voice="x"
    )
    state.part_files["2-2:Y.m4b"] = "X (2).m4b"  # part 2 owns this from an earlier run
    cfg = Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(out_dir / "book.m4b"),
        work_dir=str(tmp_path),
        batch_size=1,
        filename_template="{chapter_title}",
    )
    batches = [(1, _chapters("X")), (2, [_chapters("X", "Y")[1]])]

    paths = Pipeline(cfg)._plan_named_outputs(batches, _METADATA, state)

    assert [os.path.basename(paths[n]) for n in (1, 2)] == ["X (3).m4b", "X (2).m4b"]


@pytest.mark.parametrize("recorded", ["../evil.m4b", "sub/dir.m4b", "sub\\dir.m4b", "..", ""])
def test_a_recorded_file_name_that_is_a_path_is_refused(tmp_path, recorded):
    state = RenderState.load_or_new(
        str(tmp_path / "state.json"), source="book.epub", title="t", narrator_voice="x"
    )
    state.part_files["1-1:A.m4b"] = recorded
    cfg = Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        batch_size=1,
        filename_template="{chapter_title}",
    )

    with pytest.raises(ValueError, match="unsafe output file name"):
        Pipeline(cfg)._plan_named_outputs([(1, _chapters("A"))], _METADATA, state)
