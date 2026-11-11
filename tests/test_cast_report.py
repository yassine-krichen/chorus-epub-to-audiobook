"""The cast report: ordering, content and safe printing."""

from __future__ import annotations

import io
import json

from rich.console import Console

from book2audio.attribution.gender import resolve_genders, resolved_genders
from book2audio.cast_report import (
    build_cast_report,
    print_cast_report,
    write_cast_report,
)
from book2audio.models import Chapter, Segment, SegmentKind
from book2audio.tts.voices import VoiceCast


def _nar(text):
    return Segment(kind=SegmentKind.NARRATION, text=text)


def _dlg(text, speaker):
    return Segment(kind=SegmentKind.DIALOGUE, text=text, speaker=speaker)


def _book(names=("Sunny", "Echo", "Cassie")):
    chapter = Chapter(
        index=1,
        title="Ch",
        segments=[
            _dlg("Hello there.", "Sunny"),
            _dlg("Who goes?", "Echo"),
            _dlg("Hm.", "Sunny"),
            _dlg("Yes.", "Cassie"),
        ]
        + [_nar("Sunny scratched his head.")] * 20
        + [_nar("Cassie raised her staff.")] * 20,
    )
    verdicts = resolve_genders(list(names), [chapter])
    cast = VoiceCast()
    cast.cast(list(names), resolved_genders(verdicts))
    return chapter, verdicts, cast


def test_characters_needing_review_come_first_then_busiest_first():
    chapter, verdicts, cast = _book()

    entries = build_cast_report(["Sunny", "Echo", "Cassie"], verdicts, [chapter], cast)

    assert [e.name for e in entries] == ["Echo", "Sunny", "Cassie"]
    assert [e.needs_review for e in entries][0] is True


def test_an_unresolved_character_is_reported_with_the_narrator_voice_and_its_source():
    chapter, verdicts, cast = _book()

    echo = build_cast_report(["Sunny", "Echo", "Cassie"], verdicts, [chapter], cast)[0]

    assert (echo.gender, echo.voice, echo.voice_source) == (None, cast.narrator_voice, "narrator")


def test_a_resolved_character_reports_gender_confidence_voice_and_a_sample_line():
    chapter, verdicts, cast = _book()

    sunny = next(
        e
        for e in build_cast_report(["Sunny", "Echo", "Cassie"], verdicts, [chapter], cast)
        if e.name == "Sunny"
    )

    assert sunny.gender == "m"
    assert sunny.lines == 2
    assert sunny.sample_line == "Hello there."
    assert sunny.voice[1] == "m"
    assert sunny.layers == {"pronoun": 20}


def test_a_character_without_lines_in_the_selected_chapters_is_left_out():
    chapter, verdicts, cast = _book()

    entries = build_cast_report(["Sunny", "Echo", "Cassie", "Absent"], verdicts, [chapter], cast)

    assert "Absent" not in [e.name for e in entries]


def test_the_written_report_has_the_fields_a_reviewer_needs(tmp_path):
    chapter, verdicts, cast = _book()
    entries = build_cast_report(["Sunny", "Echo", "Cassie"], verdicts, [chapter], cast)

    path = write_cast_report(
        str(tmp_path), entries, voice_mode="cast", narrator_voice=cast.narrator_voice
    )

    data = json.loads(open(path, encoding="utf-8").read())
    sunny = next(c for c in data["characters"] if c["name"] == "Sunny")
    assert path.endswith("cast_report.json")
    assert data["provider"] == "kokoro" and data["confidence_threshold"] == 0.6
    assert set(sunny) >= {
        "lines", "gender", "confidence", "layer_counts", "top_evidence", "sample_line",
        "voice", "provider",
    }
    assert sunny["top_evidence"][0]["layer"] == "pronoun"


def test_names_and_lines_that_look_like_rich_markup_print_literally():
    chapter = Chapter(
        index=1,
        title="Ch",
        segments=[_dlg("[/] [red]boo[/]", "[bold]Mr. X[/bold]")],
    )
    verdicts = resolve_genders(["[bold]Mr. X[/bold]"], [chapter])
    cast = VoiceCast()
    entries = build_cast_report(["[bold]Mr. X[/bold]"], verdicts, [chapter], cast)
    buffer = io.StringIO()

    print_cast_report(
        Console(file=buffer, width=200), entries, voice_mode="cast", narrator_voice="am_michael"
    )

    out = buffer.getvalue()
    assert "[bold]Mr. X[/bold]" in out
    assert "[/] [red]boo[/]" in out


def test_the_printed_report_points_to_the_gender_flag_for_unresolved_characters():
    chapter, verdicts, cast = _book()
    entries = build_cast_report(["Sunny", "Echo", "Cassie"], verdicts, [chapter], cast)
    buffer = io.StringIO()

    print_cast_report(
        Console(file=buffer, width=200), entries, voice_mode="cast", narrator_voice="am_michael"
    )

    assert "--gender NAME=m or NAME=f" in buffer.getvalue()
