"""Who a dialogue tag names: subject vs object, paragraphs, lead-ins, pronouns, scare quotes.

Every case is a synthetic paraphrase of a structure that failed on a real book, built
through the real segmenter so paragraph boundaries are what the pipeline sees.
"""

from __future__ import annotations

from book2audio.attribution import HeuristicAttributor
from book2audio.models import NARRATOR, Chapter, SegmentKind
from book2audio.text import segment_paragraphs


def _run(*paragraphs: str) -> tuple[list[str], list[str]]:
    """Speakers and tiers of every dialogue line, in reading order."""
    chapter = Chapter(index=1, title="Ch", segments=segment_paragraphs(list(paragraphs)))
    attributor = HeuristicAttributor()
    attributor.attribute([chapter])
    positions = [i for i, s in enumerate(chapter.segments) if s.kind == SegmentKind.DIALOGUE]
    speakers = [chapter.segments[i].speaker or "" for i in positions]
    tiers = [attributor.tiers[(1, i)] for i in positions]
    return speakers, tiers


# --- the name must be the subject of the speech verb --------------------------------


def test_the_subject_of_a_tag_speaks_not_the_person_addressed():
    speakers, tiers = _run('"We should leave," Mara said to Tom.')

    assert speakers == ["Mara"] and tiers == ["named_trailing"]


def test_a_name_after_the_speech_verb_is_the_addressee_when_a_subject_comes_first():
    speakers, _ = _run('"Are you ready?" Mara asked Tom.')

    assert speakers == ["Mara"]


def test_an_inverted_tag_names_the_speaker():
    speakers, _ = _run('"Are you ready?" asked Tom.')

    assert speakers == ["Tom"]


def test_a_pronoun_subject_does_not_hand_the_line_to_the_name_after_the_verb():
    speakers, tiers = _run('"Are you ready?" she asked Tom.')

    assert "Tom" not in speakers and tiers == ["fallback"]


def test_a_name_inside_a_prepositional_phrase_is_not_the_subject():
    speakers, _ = _run('The guard in front of Tom chuckled.', '"Why bother?"')

    assert speakers == [NARRATOR]


def test_a_told_tag_names_who_did_the_telling():
    speakers, _ = _run('"Leave now," Mara told Tom.')

    assert speakers == ["Mara"]


# --- a tag belongs to the paragraph it sits in ---------------------------------------


def test_narration_in_the_next_paragraph_is_not_a_trailing_tag():
    speakers, tiers = _run(
        'Mara waited. "Come in," said Mara.',
        '"Thank you."',
        'Tom smiled and sat down.',
    )

    assert speakers[0] == "Mara"
    assert tiers[1] != "named_trailing"


def test_a_colon_lead_in_belongs_to_the_next_quote_not_the_previous_one():
    speakers, _ = _run(
        'Tom nodded. "Fine," said Tom.',
        'After a pause, Mara answered:',
        '"Then we go."',
    )

    assert speakers == ["Tom", "Mara"]


def test_a_short_action_beat_by_a_known_speaker_points_at_the_next_quote():
    speakers, tiers = _run(
        '"Hello," said Mara.',
        '"Hi," said Tom.',
        'Mara shrugged.',
        '"Whatever you like."',
    )

    assert speakers == ["Mara", "Tom", "Mara"]
    assert tiers[2] == "named_beat"


def test_an_action_beat_needs_a_name_that_is_already_a_known_speaker():
    # "Looking" opens the sentence like a name would; nobody called that has spoken.
    speakers, tiers = _run('Looking confused, she paused.', '"Wait."')

    assert tiers == ["fallback"] and speakers == [NARRATOR]


def test_a_tag_between_two_quotes_of_one_paragraph_speaks_for_both():
    speakers, tiers = _run('"We go," Mara said, "and we do not look back."')

    assert speakers == ["Mara", "Mara"]
    assert tiers == ["named_trailing", "continuation"]


# --- pronouns ------------------------------------------------------------------------


def _pair_with_genders() -> list[str]:
    """Mara is a 'she' and Tom a 'he' throughout, with enough text to commit."""
    story = []
    for _ in range(4):
        story += [
            'Mara raised her hand. She smiled and her eyes were bright.',
            'Tom raised his hand. He frowned and his voice was low.',
        ]
    return story


def test_a_she_tag_skips_the_male_speaker_who_spoke_last():
    speakers, tiers = _run(
        *_pair_with_genders(),
        '"Ready?" said Mara.',
        '"Not yet," said Tom.',
        'After a moment, she said:',
        '"Then I wait."',
    )

    assert speakers[-1] == "Mara" and tiers[-1] == "pronoun"


def test_a_he_tag_skips_the_female_speaker_who_spoke_last():
    speakers, _ = _run(
        *_pair_with_genders(),
        '"Not yet," said Tom.',
        '"Ready?" said Mara.',
        'After a moment, he said:',
        '"Then I wait."',
    )

    assert speakers[-1] == "Tom"


def test_a_pronoun_nobody_known_can_match_is_not_given_to_the_wrong_gender():
    speakers, tiers = _run(
        *_pair_with_genders(),
        '"Not yet," said Tom.',
        'Then, without looking up, she added:',
        '"Hurry."',
    )

    assert speakers[-1] == NARRATOR and tiers[-1] == "fallback"


def test_the_pronoun_tag_ignores_a_pronoun_from_an_earlier_clause():
    speakers, _ = _run(
        *_pair_with_genders(),
        '"Not yet," said Tom.',
        '"Ready?" said Mara.',
        'Turning away so that she could not see him, he said:',
        '"Not yet."',
    )

    assert speakers[-1] == "Tom"


# --- unnamed speakers ----------------------------------------------------------------


def test_a_described_unnamed_speaker_is_never_given_to_the_nearest_named_character():
    speakers, tiers = _run(
        '"Hello," said Tom.',
        'The old guard grunted.',
        '"Papers."',
    )

    assert speakers[1] == NARRATOR and tiers[1] == "unnamed"


# --- vocatives -----------------------------------------------------------------------


def test_a_line_that_addresses_a_character_by_name_is_not_theirs():
    speakers, _ = _run(
        '"Ready?" said Mara.',
        '"Yes," said Tom.',
        '"Tom, wait for me."',
    )

    assert speakers[2] != "Tom"


# --- scare quotes --------------------------------------------------------------------


def test_a_quoted_word_inside_a_sentence_is_read_by_the_narrator():
    speakers, tiers = _run(
        '"Hello," said Tom.',
        'It was the kind of "luck" that people envy.',
    )

    assert speakers[1] == NARRATOR and tiers[1] == "quoted_term"


def test_a_quote_without_punctuation_after_a_speech_verb_is_still_speech():
    speakers, _ = _run('Tom said "yes" and left.')

    assert speakers == ["Tom"]


def test_a_quote_with_closing_punctuation_is_never_a_scare_quote():
    _, tiers = _run('Nothing was said. "Fine." Silence.')

    assert tiers[0] != "quoted_term"


# --- aliases: spelling variants and epithets --------------------------------------------


def _attribute(*paragraphs: str) -> tuple[HeuristicAttributor, list[str], Chapter]:
    chapter = Chapter(index=1, title="Ch", segments=segment_paragraphs(list(paragraphs)))
    attributor = HeuristicAttributor()
    names = attributor.attribute([chapter])
    return attributor, names, chapter


def test_an_epithet_the_text_ties_to_a_name_folds_into_that_name():
    attributor, names, chapter = _attribute(
        'Mara, the Silver Fox, was tired.',
        '"Move," said Mara.',
        '"No," said Tom.',
        'The Silver Fox looked up.',
        '"Fine," said Silver Fox.',
    )

    assert attributor.aliases == {"Silver Fox": "Mara"}
    assert names == ["Mara", "Tom"]
    assert [s.speaker for s in chapter.segments if s.kind == SegmentKind.DIALOGUE] == [
        "Mara", "Tom", "Mara",
    ]


def test_an_epithet_written_before_the_name_is_tied_to_it():
    attributor, _, _ = _attribute(
        'Silver Fox Mara was tired.',
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
    )

    assert attributor.aliases == {"Silver Fox": "Mara"}


def test_a_known_as_phrase_ties_an_epithet_to_a_name():
    attributor, _, _ = _attribute(
        'Mara, known as the Silver Fox, was tired.',
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
    )

    assert attributor.aliases == {"Silver Fox": "Mara"}


def test_two_speakers_with_no_text_tying_them_together_stay_separate():
    attributor, names, _ = _attribute(
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
    )

    assert attributor.aliases == {} and names == ["Mara", "Silver Fox"]


def test_the_epithet_link_needs_both_names_to_be_speakers():
    attributor, names, _ = _attribute(
        'Mara, the Silver Fox, was tired.',
        '"Move," said Mara.',
    )

    assert attributor.aliases == {} and names == ["Mara"]


def test_the_gender_evidence_of_an_epithet_counts_for_the_character():
    attributor, _, _ = _attribute(
        'Mara, the Silver Fox, was tired.',
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
        *['The Silver Fox raised her hand. She smiled.'] * 4,
    )

    assert attributor.genders == {"Mara": "f"}


def test_spelling_variants_that_speak_in_different_scenes_of_one_chapter_merge():
    attributor, names, _ = _attribute(
        '"Hello," said Cassia.',
        '***',
        '"Hi," said Cassie.',
        'Cassia smiled.',
    )

    assert attributor.aliases in ({"Cassia": "Cassie"}, {"Cassie": "Cassia"})
    assert len(names) == 1


def test_two_names_one_vowel_apart_that_talk_to_each_other_stay_two_people():
    attributor, names, _ = _attribute(
        '"Where were you?" Ellen asked.',
        '"Out," Allen said.',
        '"Out where?" Ellen asked.',
        '"Just out," Allen said.',
    )

    assert attributor.aliases == {} and sorted(names) == ["Allen", "Ellen"]


def test_spelling_variants_of_opposite_gender_are_never_merged():
    ellen = Chapter(
        index=1,
        title="One",
        segments=segment_paragraphs(
            ['"Hi," said Ellen.', *['Ellen raised her hand. She smiled and her eyes were bright.'] * 5]
        ),
    )
    allen = Chapter(
        index=2,
        title="Two",
        segments=segment_paragraphs(
            ['"Hi," said Allen.', *['Allen raised his hand. He smiled and his eyes were bright.'] * 5]
        ),
    )
    attributor = HeuristicAttributor()

    names = attributor.attribute([ellen, allen])

    assert attributor.aliases == {} and sorted(names) == ["Allen", "Ellen"]


# --- epithet links must not fold two different people ------------------------------------


def test_a_list_of_people_is_not_an_epithet_link():
    attributor, names, _ = _attribute(
        '"Come here," said Aunt Polly.',
        '"I will not," said Tom.',
        'Tom, Aunt Polly and Sid walked to church.',
    )

    assert attributor.aliases == {} and sorted(names) == ["Aunt Polly", "Tom"]


def test_a_name_before_a_comma_is_not_an_epithet_without_the_article():
    attributor, names, _ = _attribute(
        '"Go away," said Rupe Collins.',
        '"No," said Penrod.',
        'After his first encounter with Rupe Collins, Penrod had become unbearable.',
    )

    assert attributor.aliases == {} and sorted(names) == ["Penrod", "Rupe Collins"]


def test_a_comma_list_of_full_names_is_not_an_epithet_link():
    attributor, names, _ = _attribute(
        '"Hello," said Maurice Levy.',
        '"Hi," said Georgie.',
        'The party was Samuel Williams, Maurice Levy, Georgie Bassett, and Herman.',
    )

    assert attributor.aliases == {} and "Maurice Levy" in names


def test_an_epithet_that_is_a_title_plus_a_name_is_never_an_epithet():
    attributor, _, _ = _attribute(
        '"Come here," said Aunt Polly.',
        '"No," said Tom.',
        'Tom, the Aunt Polly, laughed.',
    )

    assert attributor.aliases == {}


def test_an_epithet_in_a_list_continuation_is_not_linked():
    attributor, _, _ = _attribute(
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
        'Then came Mara, the Silver Fox, Tom and Sid.',
    )

    assert attributor.aliases == {}


def test_an_epithet_of_the_opposite_gender_is_not_folded():
    attributor, names, _ = _attribute(
        'Mara, the Silver Fox, was tired.',
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
        *['Mara raised her hand. She smiled and her eyes were bright.'] * 5,
        *['The Silver Fox raised his hand. He smiled and his eyes were bright.'] * 5,
    )

    assert attributor.aliases == {} and sorted(names) == ["Mara", "Silver Fox"]


def test_a_sentence_start_the_still_ties_an_epithet_to_a_name():
    attributor, _, _ = _attribute(
        'The Silver Fox, Mara, was tired.',
        '"Move," said Mara.',
        '"Fine," said Silver Fox.',
    )

    assert attributor.aliases == {"Silver Fox": "Mara"}


def test_spelling_variants_named_together_are_two_people():
    attributor, names, _ = _attribute(
        '"Hello," said Cassia.',
        '"Hi," said Cassie.',
        'Cassia hugged Cassie.',
    )

    assert attributor.aliases == {} and sorted(names) == ["Cassia", "Cassie"]


def test_a_prefix_nickname_of_the_opposite_gender_is_not_folded():
    attributor, names, _ = _attribute(
        '"Hi," said Neph.',
        '"Hello," said Nephis.',
        *['Neph raised his hand. He smiled and his eyes were bright.'] * 5,
        *['Nephis raised her hand. She smiled and her eyes were bright.'] * 5,
    )

    assert attributor.aliases == {} and sorted(names) == ["Neph", "Nephis"]
