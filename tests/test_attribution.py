from book2audio.attribution import HeuristicAttributor
from book2audio.models import NARRATOR, Chapter, Segment, SegmentKind


def _chapter(segments):
    return Chapter(index=1, title="Ch", segments=segments)


def _dlg(text):
    return Segment(kind=SegmentKind.DIALOGUE, text=text)


def _nar(text):
    return Segment(kind=SegmentKind.NARRATION, text=text)


def test_trailing_named_tag_attributes_speaker():
    ch = _chapter([_dlg("So bitter!"), _nar("muttered Sunny, grimacing.")])
    HeuristicAttributor().attribute([ch])
    assert ch.segments[0].speaker == "Sunny"


def test_leading_named_tag_attributes_speaker():
    ch = _chapter([_nar("Nephis said,"), _dlg("We move at dawn.")])
    HeuristicAttributor().attribute([ch])
    assert ch.segments[1].speaker == "Nephis"


def test_narration_is_always_the_narrator():
    ch = _chapter([_nar("The station was quiet.")])
    HeuristicAttributor().attribute([ch])
    assert ch.segments[0].speaker == NARRATOR


def test_untagged_exchange_alternates_between_two_speakers():
    ch = _chapter(
        [
            _dlg("Who are you?"),
            _nar("asked Sunny."),
            _dlg("A friend."),
            _nar("replied Nephis."),
            _dlg("Prove it."),  # untagged -> back to Sunny
        ]
    )
    HeuristicAttributor().attribute([ch])
    assert ch.segments[4].speaker == "Sunny"


def test_grammar_words_are_not_treated_as_speakers():
    ch = _chapter([_dlg("Careful."), _nar("When Sunny turned, he froze.")])
    names = HeuristicAttributor().attribute([ch])
    assert "When" not in names


def test_prefix_nickname_merges_into_full_name():
    ch = _chapter(
        [
            _dlg("Go."),
            _nar("said Nephis."),
            _dlg("Wait."),
            _nar("Neph hesitated."),
        ]
    )
    names = HeuristicAttributor().attribute([ch])
    assert "Nephis" in names
    assert "Neph" not in names


def test_gender_inferred_from_dialogue_tag_pronouns():
    ch = _chapter([_dlg("Now."), _nar("said Sunny. He was already gone.")])
    attr = HeuristicAttributor()
    attr.attribute([ch])
    assert ch.segments[0].speaker == "Sunny"
    assert attr.genders.get("Sunny") == "m"
