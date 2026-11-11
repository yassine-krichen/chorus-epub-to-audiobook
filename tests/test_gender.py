"""Tests for the layered gender resolver (attribution/gender.py)."""

from __future__ import annotations

import pytest

from book2audio.attribution import HeuristicAttributor
from book2audio.attribution.gender import (
    GENDER_CONFIDENCE_THRESHOLD,
    resolve_genders,
    resolved_genders,
    validate_gender_overrides,
    wilson_lower_bound,
)
from book2audio.models import Chapter, Segment, SegmentKind


def _nar(text):
    return Segment(kind=SegmentKind.NARRATION, text=text)


def _dlg(text):
    return Segment(kind=SegmentKind.DIALOGUE, text=text)


def _chapter(*segments, index=1):
    return Chapter(index=index, title="Ch", segments=list(segments))


def _narration(*sentences, repeat=1):
    return _chapter(*[_nar(s) for s in sentences for _ in range(repeat)])


def _verdict(names, chapter, name, **kwargs):
    return resolve_genders(names, [chapter], **kwargs)[name]


# --- L1: pronouns ----------------------------------------------------------------


def test_the_confidence_threshold_is_point_six():
    assert GENDER_CONFIDENCE_THRESHOLD == 0.6


def test_a_pronoun_after_a_name_in_plain_narration_counts():
    chapter = _narration("Emilia lifted her chin and squared her shoulders.", repeat=8)

    verdict = _verdict(["Emilia"], chapter, "Emilia")

    assert verdict.gender == "f"
    assert verdict.confidence >= GENDER_CONFIDENCE_THRESHOLD
    assert verdict.evidence[0].layer == "pronoun"


def test_a_male_possessive_after_a_name_in_narration_counts_for_that_name():
    chapter = _narration("Sunny scratched his head.", repeat=8)

    assert _verdict(["Sunny"], chapter, "Sunny").gender == "m"


def test_says_name_with_her_head_held_high_counts_for_the_name():
    chapter = _chapter(
        *[x for _ in range(8) for x in (_dlg("I will go."), _nar("says Emilia with her head held high."))]
    )

    verdict = _verdict(["Emilia"], chapter, "Emilia")

    assert verdict.gender == "f"
    assert verdict.evidence[0].snippet == "says Emilia with her head held high."


def test_an_object_pronoun_is_not_counted():
    chapter = _narration(
        "Sunny helped him up.",
        "Sunny looked at her.",
        "Sunny told her to wait.",
        "Sunny gave her and Kai a look.",
        repeat=4,
    )

    verdict = _verdict(["Sunny", "Kai"], chapter, "Sunny")

    assert verdict.evidence == ()
    assert verdict.gender is None


def test_her_before_a_noun_is_a_possessive_and_counts():
    chapter = _narration("Nephis raised her sword.", repeat=8)

    assert _verdict(["Nephis"], chapter, "Nephis").gender == "f"


def test_pronouns_inside_dialogue_quotes_are_not_counted():
    # Sunny's "I got her!" and a speech about a male hero must not move anyone.
    chapter = _chapter(
        *[
            seg
            for _ in range(8)
            for seg in (
                _nar("Sunny shouted."),
                _dlg("I got her! She is safe and he is gone, his sword too."),
            )
        ]
    )

    verdict = _verdict(["Sunny"], chapter, "Sunny")

    assert verdict.evidence == ()
    assert verdict.gender is None


def test_pronouns_inside_single_quoted_thoughts_are_not_counted():
    chapter = _narration("Sunny frowned. 'She is lying and he knows it.'", repeat=8)

    assert _verdict(["Sunny"], chapter, "Sunny").evidence == ()


def test_the_next_sentence_is_ignored_when_the_previous_one_names_two_characters():
    chapter = _narration("Sunny watched Nephis. She smiled and tilted her head.", repeat=8)

    sunny = _verdict(["Sunny", "Nephis"], chapter, "Sunny")

    assert sunny.gender is None
    assert all(e.gender != "f" for e in sunny.evidence)


def test_the_next_sentence_counts_at_half_weight_when_the_previous_one_names_one_character():
    chapter = _narration("Nephis waited. She tilted her head.", repeat=1)

    verdict = _verdict(["Nephis"], chapter, "Nephis")

    assert [e.weight for e in verdict.evidence] == [0.5, 0.5]


def test_abbreviated_titles_do_not_split_a_sentence():
    chapter = _narration("Mr. Williams stood up and took his hat.", repeat=8)

    assert _verdict(["Mr. Williams"], chapter, "Mr. Williams").gender == "m"


def test_pronoun_tier_dialogue_lines_do_not_vote():
    # "she said" is resolved to Sunny only by guessing (pronoun tier); that guess
    # must not turn into a female vote for Sunny.
    chapter = _chapter(
        *[
            seg
            for _ in range(8)
            for seg in (_dlg("Hello."), _nar("said Sunny."), _dlg("Fine."), _nar("she said."))
        ]
    )
    attributor = HeuristicAttributor()

    attributor.attribute([chapter])

    assert "pronoun" in attributor.tiers.values()
    assert attributor.verdicts["Sunny"].evidence == ()
    assert attributor.genders == {}


# --- L2: titles, kinship, appositives ---------------------------------------------


def test_mrs_title_resolves_female_from_a_single_mention():
    chapter = _narration("Mrs. Schofield smiled.")

    verdict = _verdict(["Mrs. Schofield"], chapter, "Mrs. Schofield")

    assert verdict.gender == "f"
    assert verdict.evidence[0].layer == "title"


def test_a_title_before_a_bare_surname_counts_for_the_full_name():
    chapter = _narration("Lady Hendry swept past, and later Mrs. Hendry sat.")

    assert _verdict(["Hendry"], chapter, "Hendry").gender == "f"


def test_his_sister_x_is_female():
    chapter = _narration("Sunny walked home with his sister Rain.")

    verdict = _verdict(["Rain"], chapter, "Rain")

    assert verdict.gender == "f"
    assert verdict.evidence[0].layer == "kinship"


def test_x_comma_her_little_brother_is_male_and_the_owner_word_does_not_vote_for_x():
    chapter = _narration("Cassie waited for Kai, her little brother.")

    verdict = _verdict(["Cassie", "Kai"], chapter, "Kai")

    assert verdict.gender == "m"
    assert verdict.female_weight == 0


def test_a_possessed_kin_word_does_not_make_the_name_that_gender():
    chapter = _narration("Nephis, her father's sword in hand, left.")

    verdict = _verdict(["Nephis"], chapter, "Nephis")

    assert {e.layer for e in verdict.evidence} == {"pronoun"}
    assert verdict.male_weight == 0


def test_x_comma_the_girl_is_female():
    chapter = _narration("Nephis, the silver-haired girl, nodded.")

    verdict = _verdict(["Nephis"], chapter, "Nephis")

    assert verdict.gender == "f"
    assert verdict.evidence[0].layer == "appositive"


def test_master_is_not_a_gendered_title():
    chapter = _narration("Master Jet bowed to the class.", repeat=3)

    verdict = _verdict(["Jet"], chapter, "Jet")

    assert verdict.gender is None
    assert verdict.evidence == ()


# --- confidence, ties, overrides ---------------------------------------------------


def test_a_tie_is_unresolved_with_low_confidence():
    chapter = _narration("Sam rubbed his eyes.", "Sam brushed her hair.", repeat=4)

    verdict = _verdict(["Sam"], chapter, "Sam")

    assert verdict.gender is None
    assert verdict.confidence < GENDER_CONFIDENCE_THRESHOLD


def test_a_couple_of_agreeing_pronouns_are_not_enough_to_commit():
    chapter = _narration("Echo shook his head.", repeat=2)

    verdict = _verdict(["Echo"], chapter, "Echo")

    assert verdict.gender is None
    assert 0 < verdict.confidence < GENDER_CONFIDENCE_THRESHOLD


def test_a_strong_title_outweighs_a_few_conflicting_pronouns():
    chapter = _narration("Mrs. Hale smiled.", "Mrs. Hale lifted his hat.")

    assert _verdict(["Mrs. Hale"], chapter, "Mrs. Hale").gender == "f"


def test_confidence_is_the_wilson_lower_bound_of_the_winning_share():
    assert wilson_lower_bound(1, 1) == pytest.approx(0.2065, abs=1e-3)
    assert wilson_lower_bound(6, 6) >= GENDER_CONFIDENCE_THRESHOLD
    assert wilson_lower_bound(0, 0) == 0.0
    chapter = _narration("Sunny scratched his head.", repeat=8)
    assert _verdict(["Sunny"], chapter, "Sunny").confidence == pytest.approx(
        wilson_lower_bound(8, 8), abs=1e-3
    )


def test_a_user_override_beats_the_inference_with_full_confidence():
    chapter = _narration("Sunny scratched his head.", repeat=20)

    verdict = _verdict(["Sunny"], chapter, "Sunny", overrides={"sunny": "f"})

    assert verdict.gender == "f"
    assert verdict.confidence == 1.0
    assert verdict.evidence[0].layer == "override"


def test_an_override_for_an_alias_reaches_the_canonical_character():
    chapter = _narration("Cassie waited.")

    verdict = _verdict(
        ["Cassie"], chapter, "Cassie", overrides={"Cassia": "f"}, aliases={"Cassia": "Cassie"}
    )

    assert verdict.gender == "f"


def test_a_name_with_no_evidence_is_unresolved_with_zero_confidence():
    verdict = _verdict(["Ghost"], _narration("Nothing here."), "Ghost")

    assert (verdict.gender, verdict.confidence, verdict.evidence) == (None, 0.0, ())


def test_evidence_records_the_chapter_and_a_short_snippet():
    long_sentence = "Sunny " + "walked far " * 40 + "and scratched his head."
    chapter = _chapter(_nar(long_sentence), index=7)

    evidence = _verdict(["Sunny"], chapter, "Sunny").evidence[0]

    assert evidence.chapter_index == 7
    assert len(evidence.snippet) <= 200


def test_resolved_genders_keeps_only_confident_characters():
    chapter = _narration("Sunny scratched his head.", repeat=8)
    verdicts = resolve_genders(["Sunny", "Ghost"], [chapter])

    assert resolved_genders(verdicts) == {"Sunny": "m"}


# --- alias merge between spellings --------------------------------------------------


def _tagged(name, chapter_index, lines=1, narration=()):
    segments = []
    for _ in range(lines):
        segments += [_dlg("Hm."), _nar(f"said {name}.")]
    segments += [_nar(text) for text in narration]
    return Chapter(index=chapter_index, title="Ch", segments=segments)


def test_a_spelling_variant_used_in_other_chapters_is_merged_and_its_narration_still_counts():
    chapters = [
        _tagged("Cassie", 1, lines=2),
        _tagged("Cassia", 2, lines=1, narration=["Cassia raised her staff."] * 8),
    ]
    attributor = HeuristicAttributor()

    names = attributor.attribute(chapters)

    assert names == ["Cassie"]
    assert attributor.aliases == {"Cassia": "Cassie"}
    assert attributor.genders == {"Cassie": "f"}
    assert {s.speaker for c in chapters for s in c.segments if s.kind == SegmentKind.DIALOGUE} == {
        "Cassie"
    }


def test_two_vowel_variants_that_speak_in_the_same_chapter_are_different_characters():
    chapter = _chapter(
        _dlg("Hello."), _nar("said Maria."), _dlg("Hi."), _nar("said Maris."), index=1
    )

    assert sorted(HeuristicAttributor().attribute([chapter])) == ["Maria", "Maris"]


def test_maria_and_mario_are_not_merged_even_when_they_never_share_a_chapter():
    chapters = [_tagged("Maria", 1), _tagged("Mario", 2)]

    assert sorted(HeuristicAttributor().attribute(chapters)) == ["Maria", "Mario"]


def test_julia_and_julio_are_never_merged():
    chapters = [_tagged("Julia", 1), _tagged("Julio", 5)]
    attributor = HeuristicAttributor()

    assert sorted(attributor.attribute(chapters)) == ["Julia", "Julio"]
    assert attributor.aliases == {}


def test_a_chain_of_variants_leaves_no_orphaned_speaker_labels():
    # Julio would take Julie, which already took Julia: every fold must end at one name.
    chapters = [
        _tagged("Julia", 1, lines=2),
        _tagged("Julie", 2, lines=3),
        _tagged("Julio", 3, lines=5),
    ]
    attributor = HeuristicAttributor()

    names = attributor.attribute(chapters)

    labels = {s.speaker for c in chapters for s in c.segments if s.kind == SegmentKind.DIALOGUE}
    assert labels <= set(names)
    assert set(attributor.aliases.values()) <= set(names)
    assert not set(attributor.aliases) & set(names)


def test_prefix_nickname_chains_also_end_at_the_final_name():
    chapters = [_tagged("Nep", 1), _tagged("Neph", 2), _tagged("Nephis", 3, lines=3)]
    attributor = HeuristicAttributor()

    names = attributor.attribute(chapters)

    assert names == ["Nephis"]
    assert set(attributor.aliases.values()) == {"Nephis"}
    labels = {s.speaker for c in chapters for s in c.segments if s.kind == SegmentKind.DIALOGUE}
    assert labels == {"Nephis"}


def test_names_that_differ_by_a_consonant_are_not_merged():
    chapter = _chapter(
        _dlg("Hello."),
        _nar("said Marco."),
        _dlg("Hi."),
        _nar("said Marta."),
    )

    assert sorted(HeuristicAttributor().attribute([chapter])) == ["Marco", "Marta"]


# --- override validation -------------------------------------------------------------


def test_validate_gender_overrides_accepts_m_and_f():
    validate_gender_overrides({"Sunny": "m", "Cassie": "f"})


@pytest.mark.parametrize("value", ["x", "M", "male", "", "unknown"])
def test_validate_gender_overrides_rejects_other_values(value):
    with pytest.raises(ValueError, match="must be 'm' or 'f'"):
        validate_gender_overrides({"Sunny": value})


def test_validate_gender_overrides_rejects_an_overlong_name():
    with pytest.raises(ValueError, match="characters"):
        validate_gender_overrides({"x" * 81: "m"})


def test_validate_gender_overrides_rejects_a_blank_name():
    with pytest.raises(ValueError, match="characters"):
        validate_gender_overrides({"  ": "m"})


def test_validate_gender_overrides_rejects_too_many_entries():
    with pytest.raises(ValueError, match="at most"):
        validate_gender_overrides({f"name{i}": "m" for i in range(101)})


# --- overrides reach both attributors ------------------------------------------------


def _speaking_chapter():
    return _chapter(_dlg("Hello."), _nar("said Echo."), _dlg("Hm."), _nar("said Echo."))


def test_the_heuristic_attributor_applies_gender_overrides_to_its_verdicts():
    attributor = HeuristicAttributor(gender_overrides={"Echo": "f"})

    attributor.attribute([_speaking_chapter()])

    assert attributor.genders == {"Echo": "f"}
    assert attributor.verdicts["Echo"].confidence == 1.0


def test_the_llm_attributor_reuses_the_resolver_and_applies_gender_overrides():
    from book2audio.attribution import LLMAttributor

    attributor = LLMAttributor("hybrid", None, gender_overrides={"Echo": "m"})

    attributor.attribute([_speaking_chapter()])

    assert attributor.genders == {"Echo": "m"}
    assert attributor.verdicts["Echo"].evidence[0].layer == "override"


# --- performance and alias lookups ----------------------------------------------------


def test_a_huge_run_of_abbreviations_is_split_in_linear_time():
    import time

    chapter = _narration("Mr. A " * 35_000)  # about 200 KB with no sentence break
    started = time.perf_counter()

    resolve_genders(["Mr. A"], [chapter])

    assert time.perf_counter() - started < 1.0


def test_an_override_for_an_alias_is_matched_case_insensitively():
    chapter = _narration("Cassie waited.")

    verdict = _verdict(
        ["Cassie"], chapter, "Cassie", overrides={"cassia": "f"}, aliases={"Cassia": "Cassie"}
    )

    assert verdict.gender == "f" and verdict.confidence == 1.0
