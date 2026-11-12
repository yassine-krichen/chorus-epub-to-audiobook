from book2audio.models import SegmentKind
from book2audio.text.segmenter import segment_paragraphs


def test_pure_narration_paragraph_is_one_narration_segment():
    segs = segment_paragraphs(["He walked into the empty station."])
    assert len(segs) == 1
    assert segs[0].kind == SegmentKind.NARRATION
    assert segs[0].text == "He walked into the empty station."


def test_paragraph_splits_into_narration_and_dialogue_in_order():
    segs = segment_paragraphs(['Sunny sighed. "So bitter!" he muttered.'])
    kinds = [s.kind for s in segs]
    assert kinds == [
        SegmentKind.NARRATION,
        SegmentKind.DIALOGUE,
        SegmentKind.NARRATION,
    ]
    assert segs[1].text == "So bitter!"  # quotes stripped


def test_curly_quotes_are_recognized_as_dialogue():
    segs = segment_paragraphs(["“Hello there,” she said."])
    assert segs[0].kind == SegmentKind.DIALOGUE
    assert segs[0].text == "Hello there,"


def test_scene_break_becomes_its_own_segment():
    segs = segment_paragraphs(["First.", "***", "Second."])
    assert [s.kind for s in segs] == [
        SegmentKind.NARRATION,
        SegmentKind.SCENE_BREAK,
        SegmentKind.NARRATION,
    ]


def test_dialogue_only_paragraph_yields_single_dialogue_segment():
    segs = segment_paragraphs(['"Are you lost, boy?"'])
    assert len(segs) == 1
    assert segs[0].kind == SegmentKind.DIALOGUE
    assert segs[0].text == "Are you lost, boy?"


def test_segments_remember_which_paragraph_they_came_from():
    segs = segment_paragraphs(['He said, "Go." Then left.', "Next paragraph.", "***", '"Hi."'])

    assert [s.paragraph for s in segs] == [0, 0, 0, 1, 2, 3]


def test_the_paragraph_number_does_not_affect_segment_equality():
    from book2audio.models import Segment, SegmentKind

    assert Segment(SegmentKind.NARRATION, "x", paragraph=3) == Segment(SegmentKind.NARRATION, "x")
