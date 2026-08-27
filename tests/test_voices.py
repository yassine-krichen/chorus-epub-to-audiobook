from book2audio.models import NARRATOR
from book2audio.tts.voices import FEMALE_VOICES, MALE_VOICES, VoiceCast


def test_narrator_uses_the_narrator_voice():
    cast = VoiceCast(narrator_voice="am_michael")
    assert cast.voice_for(NARRATOR) == "am_michael"


def test_leads_get_distinct_voices():
    cast = VoiceCast()
    cast.cast(["Sunny", "Nephis", "Cassie", "Rain"])
    voices = [cast.voice_for(n) for n in ["Sunny", "Nephis", "Cassie", "Rain"]]
    assert len(set(voices)) == 4


def test_assignment_is_stable_for_the_same_name():
    a, b = VoiceCast(), VoiceCast()
    a.cast(["Sunny", "Nephis"])
    b.cast(["Sunny", "Nephis"])
    assert a.voice_for("Sunny") == b.voice_for("Sunny")


def test_gender_guess_selects_matching_pool():
    cast = VoiceCast()
    cast.cast(["Sunny", "Cassie"], genders={"Sunny": "m", "Cassie": "f"})
    assert cast.voice_for("Sunny") in MALE_VOICES
    assert cast.voice_for("Cassie") in FEMALE_VOICES


def test_override_wins_over_automatic_casting():
    cast = VoiceCast(overrides={"Sunny": "bm_george"})
    cast.cast(["Sunny", "Nephis"])
    assert cast.voice_for("Sunny") == "bm_george"
