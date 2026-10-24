"""Tests for the confidence tiers the heuristic attributor records per line."""

from __future__ import annotations

from book2audio.attribution import HeuristicAttributor
from book2audio.attribution.llm import UNSURE_TIERS
from book2audio.models import Chapter, Segment, SegmentKind


def _dlg(text):
    return Segment(kind=SegmentKind.DIALOGUE, text=text)


def _nar(text):
    return Segment(kind=SegmentKind.NARRATION, text=text)


def test_each_resolution_path_records_its_tier():
    chapter = Chapter(
        index=7,
        title="Ch",
        segments=[
            _dlg("Who are you?"),  # 0: trailing name
            _nar("asked Sunny."),
            _nar("Nephis said,"),
            _dlg("A friend."),  # 3: leading name
            _dlg("Prove it."),  # 4: two known speakers -> alternation
            _nar("Rain fell."),
            _dlg("Fine."),  # 6: pronoun tag
            _nar("she said."),
        ],
    )
    attributor = HeuristicAttributor()

    attributor.attribute([chapter])

    assert attributor.tiers[(7, 0)] == "named_trailing"
    assert attributor.tiers[(7, 3)] == "named_leading"
    assert attributor.tiers[(7, 4)] == "alternation"
    assert attributor.tiers[(7, 6)] == "pronoun"


def test_a_line_with_no_known_speaker_is_a_fallback():
    chapter = Chapter(index=1, title="Ch", segments=[_dlg("Hello?")])
    attributor = HeuristicAttributor()

    attributor.attribute([chapter])

    assert attributor.tiers == {(1, 0): "fallback"}


def test_tiers_are_keyed_per_chapter_and_only_cover_dialogue():
    first = Chapter(index=1, title="A", segments=[_nar("Rain."), _dlg("Hm.")])
    second = Chapter(index=2, title="B", segments=[_dlg("Ha.")])
    attributor = HeuristicAttributor()

    attributor.attribute([first, second])

    assert set(attributor.tiers) == {(1, 1), (2, 0)}


def test_tiers_are_reset_between_runs():
    attributor = HeuristicAttributor()
    attributor.attribute([Chapter(index=1, title="A", segments=[_dlg("Hm.")])])

    attributor.attribute([Chapter(index=2, title="B", segments=[_dlg("Ha.")])])

    assert set(attributor.tiers) == {(2, 0)}


def test_unsure_tiers_are_the_guessed_ones():
    assert UNSURE_TIERS == {"pronoun", "alternation", "fallback"}


def test_infer_genders_is_public_and_returns_the_vote():
    chapter = Chapter(
        index=1,
        title="Ch",
        segments=[_dlg("Hello."), _nar("said Sunny, and he smiled.")],
    )
    attributor = HeuristicAttributor()
    attributor.attribute([chapter])

    assert attributor.infer_genders(["Sunny"], [chapter]) == {"Sunny": "m"}
    assert attributor.genders == {"Sunny": "m"}
