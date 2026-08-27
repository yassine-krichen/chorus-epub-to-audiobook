from book2audio.text.normalize import normalize_for_tts


def test_em_dash_becomes_a_comma_pause():
    assert normalize_for_tts("wait—stop") == "wait, stop"


def test_spaced_hyphen_dash_becomes_comma_pause():
    assert normalize_for_tts("the real deal - the good kind") == (
        "the real deal, the good kind"
    )


def test_smart_quotes_are_stripped():
    assert normalize_for_tts("“hi” ‘there’") == "hi 'there'"


def test_whitespace_is_collapsed():
    assert normalize_for_tts("a\n\n  b\tc") == "a b c"
