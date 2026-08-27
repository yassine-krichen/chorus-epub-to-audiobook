from book2audio.text.expressiveness import speech_params


def test_exclamation_is_faster_and_louder():
    speed, gain = speech_params("Stop!")
    assert speed > 1.0
    assert gain > 1.0


def test_trailing_off_is_slower_and_softer():
    speed, gain = speech_params("I don't know...")
    assert speed < 1.0
    assert gain < 1.0


def test_curly_ellipsis_is_also_trailing_off():
    speed, gain = speech_params("Well…")
    assert speed < 1.0
    assert gain < 1.0


def test_question_is_slightly_slower_same_volume():
    speed, gain = speech_params("Who are you?")
    assert speed < 1.0
    assert gain == 1.0


def test_neutral_text_is_unchanged():
    assert speech_params("He walked into the room.") == (1.0, 1.0)


def test_multiple_exclamation_marks_still_match():
    speed, gain = speech_params("No!!!")
    assert speed > 1.0
    assert gain > 1.0
