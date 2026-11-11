"""The user's cast fixes: validating character merges and applying them in the pipeline."""

from __future__ import annotations

import pytest

import book2audio.pipeline as pipeline_module
from book2audio.cast_review import merge_characters, validate_character_aliases
from book2audio.config import Config
from book2audio.epub import ParsedChapter
from book2audio.models import NARRATOR, BookMetadata, Chapter, Segment, SegmentKind
from book2audio.pipeline import Pipeline

_BOOK = BookMetadata(title="Book", author="Author")


def _dialogue(text: str, speaker: str) -> Segment:
    return Segment(kind=SegmentKind.DIALOGUE, text=text, speaker=speaker)


# --- validate_character_aliases -----------------------------------------------------


def test_a_plain_merge_is_valid():
    validate_character_aliases({"Rain": "Cassie"})  # must not raise


def test_merging_into_the_narrator_is_valid():
    validate_character_aliases({"Rain": NARRATOR})  # must not raise


@pytest.mark.parametrize(
    "aliases",
    [
        {"Rain": "rain"},  # into itself, ignoring case
        {"": "Cassie"},
        {"Rain": "  "},
        {"x" * 81: "Cassie"},
        {"Rain": "x" * 81},
        {f"Name {i}": "Cassie" for i in range(101)},
    ],
)
def test_a_bad_merge_is_refused(aliases):
    with pytest.raises(ValueError):
        validate_character_aliases(aliases)


def test_a_chain_of_merges_is_refused_because_the_middle_character_would_vanish():
    with pytest.raises(ValueError, match="merged into another character"):
        validate_character_aliases({"Rain": "Cassie", "Cassie": "Sunny"})


# --- merge_characters -----------------------------------------------------------------


def test_merging_hands_the_lines_to_the_target_and_reports_what_changed():
    chapter = Chapter(
        index=1,
        title="Ch",
        segments=[_dialogue("Hm.", "Rain"), _dialogue("Yes.", "Cassie"), _dialogue("No.", "Sunny")],
    )

    applied = merge_characters([chapter], {"Rain": "Cassie"})

    assert [s.speaker for s in chapter.segments] == ["Cassie", "Cassie", "Sunny"]
    assert applied == {"Rain": "Cassie"}


def test_a_name_that_speaks_nowhere_is_not_reported_as_applied():
    chapter = Chapter(index=1, title="Ch", segments=[_dialogue("Hm.", "Sunny")])

    assert merge_characters([chapter], {"Ghost": "Cassie"}) == {}


def test_narration_is_never_relabelled():
    narration = Segment(kind=SegmentKind.NARRATION, text="Rain looked up.", speaker=NARRATOR)
    chapter = Chapter(index=1, title="Ch", segments=[narration])

    merge_characters([chapter], {NARRATOR: "Cassie"})

    assert narration.speaker == NARRATOR


# --- Config -----------------------------------------------------------------------------


def test_config_validation_rejects_a_chain_of_character_merges(tmp_path):
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"x")
    config = Config(
        epub_path=str(epub),
        output_path=str(tmp_path / "b.m4b"),
        work_dir=str(tmp_path),
        character_aliases={"A": "B", "B": "C"},
    )

    with pytest.raises(ValueError, match="merged into another character"):
        config.validate()


def test_config_defaults_to_no_merges_and_no_review():
    config = Config(epub_path="b.epub", output_path="b.m4b", work_dir=".")

    assert config.character_aliases == {}
    assert config.cast_review is False


# --- Pipeline -----------------------------------------------------------------------------


def _parsed() -> list[ParsedChapter]:
    return [
        ParsedChapter(
            index=1,
            title="Chapter 1",
            paragraphs=[
                '"So bitter!" said Sunny.',
                *["Sunny scratched his head and shrugged his shoulders."] * 8,
                '"Quiet," said Cassie.',
                *["Cassie raised her staff and straightened her cloak."] * 8,
                '"Hm," said Rain.',
                '"We move at dawn," said Rain.',
            ],
        )
    ]


def _pipeline(tmp_path, monkeypatch, **overrides) -> Pipeline:
    monkeypatch.setattr(pipeline_module, "parse_epub", lambda path, cover_dir: (_BOOK, _parsed()))
    config = Config(
        epub_path=str(tmp_path / "book.epub"),
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        music=False,
        **overrides,
    )
    return Pipeline(config)


def _speakers(book) -> list[str]:
    return [s.speaker for s in book.chapters[0].segments if s.kind == SegmentKind.DIALOGUE]


def test_without_merges_every_detected_character_stays(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch)

    pipeline._load_and_prepare()

    assert set(pipeline._characters) == {"Sunny", "Cassie", "Rain"}


def test_a_merged_character_gives_its_lines_to_the_target(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Rain": "Cassie"})

    book = pipeline._load_and_prepare()

    assert _speakers(book) == ["Sunny", "Cassie", "Cassie", "Cassie"]
    assert set(pipeline._characters) == {"Sunny", "Cassie"}
    # The busiest speaker leads, as in an unmerged run.
    assert pipeline._characters[0] == "Cassie"


def test_a_merged_character_is_cast_with_its_target_voice(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Rain": "Cassie"})
    book = pipeline._load_and_prepare()

    cast = pipeline._cast_voices(book)

    assert cast.voice_for("Cassie")[1] == "f"
    assert cast.voice_for("Rain") == cast.narrator_voice  # nobody speaks as Rain any more


def test_the_gender_of_a_merged_cast_is_worked_out_again_for_the_target(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Rain": "Cassie"})

    pipeline._load_and_prepare()

    assert pipeline._genders["Cassie"] == "f"
    assert "Rain" not in pipeline._verdicts


def test_the_narrator_can_take_over_a_character(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Rain": NARRATOR})

    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    assert _speakers(book) == ["Sunny", "Cassie", NARRATOR, NARRATOR]
    assert set(pipeline._characters) == {"Sunny", "Cassie"}
    assert cast.voice_for(NARRATOR) == cast.narrator_voice


def test_a_merge_for_a_name_nobody_speaks_as_changes_nothing(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Ghost": "Cassie"})

    book = pipeline._load_and_prepare()

    assert _speakers(book) == ["Sunny", "Cassie", "Rain", "Rain"]
    assert set(pipeline._characters) == {"Sunny", "Cassie", "Rain"}


def test_a_gender_override_still_applies_to_a_merged_target(tmp_path, monkeypatch):
    pipeline = _pipeline(
        tmp_path,
        monkeypatch,
        character_aliases={"Rain": "Sunny"},
        gender_overrides={"Sunny": "f"},
    )

    pipeline._load_and_prepare()

    assert pipeline._genders["Sunny"] == "f"


def test_the_cast_report_lists_the_merged_character_once(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch, character_aliases={"Rain": "Cassie"})
    book = pipeline._load_and_prepare()
    cast = pipeline._cast_voices(book)

    from book2audio.cast_report import build_cast_report

    entries = build_cast_report(pipeline._characters, pipeline._verdicts, book.chapters, cast)

    by_name = {entry.name: entry for entry in entries}
    assert set(by_name) == {"Sunny", "Cassie"}
    assert by_name["Cassie"].lines == 3
