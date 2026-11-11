from book2audio.models import NARRATOR
from book2audio.tts.voices import FEMALE_VOICES, MALE_VOICES, VoiceCast


def test_narrator_uses_the_narrator_voice():
    cast = VoiceCast(narrator_voice="am_michael")
    assert cast.voice_for(NARRATOR) == "am_michael"


def test_leads_of_the_same_gender_get_distinct_voices():
    genders = {"Sunny": "m", "Nephis": "f", "Cassie": "f", "Kai": "m"}
    cast = VoiceCast()
    cast.cast(list(genders), genders=genders)
    voices = [cast.voice_for(n) for n in genders]
    assert len(set(voices)) == 4


def test_assignment_is_stable_for_the_same_name():
    genders = {"Sunny": "m", "Nephis": "f"}
    a, b = VoiceCast(), VoiceCast()
    a.cast(["Sunny", "Nephis"], genders)
    b.cast(["Sunny", "Nephis"], genders)
    assert a.voice_for("Sunny") == b.voice_for("Sunny")


def test_a_character_with_unresolved_gender_is_read_by_the_narrator_voice():
    cast = VoiceCast(narrator_voice="am_michael")
    cast.cast(["Sunny", "Echo"], genders={"Sunny": "m"})
    assert cast.voice_for("Echo") == "am_michael"
    assert "Echo" not in cast.as_dict()


def test_unresolved_characters_never_take_a_voice_from_the_gender_pools():
    cast = VoiceCast(narrator_voice="bf_emma")
    cast.cast(["A", "B", "C"], genders={})
    assert {cast.voice_for(n) for n in "ABC"} == {"bf_emma"}


def test_a_speaker_never_cast_gets_the_narrator_voice_not_a_hashed_pool_voice():
    cast = VoiceCast(narrator_voice="am_michael")
    assert cast.voice_for("Someone New") == "am_michael"
    assert cast.voice_for("Someone New") == "am_michael"
    assert cast.as_dict() == {NARRATOR: "am_michael"}


def test_an_override_beats_the_narrator_fallback_for_an_unresolved_character():
    cast = VoiceCast(overrides={"Echo": "bf_emma"})
    cast.cast(["Echo"], genders={})
    assert cast.voice_for("Echo") == "bf_emma"


def test_an_override_beats_the_inferred_gender_pool():
    cast = VoiceCast(overrides={"Cassie": "am_adam"})
    cast.cast(["Cassie"], genders={"Cassie": "f"})
    assert cast.voice_for("Cassie") == "am_adam"


def test_gender_guess_selects_matching_pool():
    cast = VoiceCast()
    cast.cast(["Sunny", "Cassie"], genders={"Sunny": "m", "Cassie": "f"})
    assert cast.voice_for("Sunny") in MALE_VOICES
    assert cast.voice_for("Cassie") in FEMALE_VOICES


def test_override_wins_over_automatic_casting():
    cast = VoiceCast(overrides={"Sunny": "bm_george"})
    cast.cast(["Sunny", "Nephis"], genders={"Sunny": "m", "Nephis": "f"})
    assert cast.voice_for("Sunny") == "bm_george"


def test_male_voice_bias_is_tried_first_for_a_male_guessed_character():
    cast = VoiceCast(male_voice="bm_george")
    cast.cast(["Sunny"], genders={"Sunny": "m"})
    assert cast.voice_for("Sunny") == "bm_george"


def test_female_voice_bias_is_tried_first_for_a_female_guessed_character():
    cast = VoiceCast(female_voice="bf_isabella")
    cast.cast(["Cassie"], genders={"Cassie": "f"})
    assert cast.voice_for("Cassie") == "bf_isabella"


def test_male_voice_bias_does_not_leak_into_the_female_pool():
    cast = VoiceCast(male_voice="bm_george")
    cast.cast(["Cassie"], genders={"Cassie": "f"})
    assert cast.voice_for("Cassie") != "bm_george"
    assert cast.voice_for("Cassie") in FEMALE_VOICES


def test_explicit_override_still_wins_over_voice_bias():
    cast = VoiceCast(male_voice="bm_george", overrides={"Sunny": "am_puck"})
    cast.cast(["Sunny"], genders={"Sunny": "m"})
    assert cast.voice_for("Sunny") == "am_puck"


def test_a_preferred_voice_not_in_the_matching_gender_pool_is_silently_ignored():
    """male_voice set to a voice with a female prefix isn't a member of
    MALE_VOICES, so the bias is dropped (not an error) and casting falls
    back to the ordinary male pool."""
    cast = VoiceCast(male_voice="af_heart")
    cast.cast(["Sunny"], genders={"Sunny": "m"})
    assert cast.voice_for("Sunny") == MALE_VOICES[0]


def test_prioritized_moves_the_preferred_voice_to_the_front_of_the_pool():
    pool = ("a", "b", "c")
    assert VoiceCast._prioritized(pool, "b") == ("b", "a", "c")


def test_prioritized_leaves_the_pool_unchanged_when_preferred_is_none():
    pool = ("a", "b", "c")
    assert VoiceCast._prioritized(pool, None) == pool


def test_prioritized_leaves_the_pool_unchanged_when_preferred_is_not_a_member():
    pool = ("a", "b", "c")
    assert VoiceCast._prioritized(pool, "z") == pool
