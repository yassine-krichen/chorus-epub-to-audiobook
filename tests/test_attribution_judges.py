"""The AI alias and gender judges: every answer must quote the book, or it is dropped.

Texts are synthetic. A fake client stands in for ``LLMClient`` and records its calls.
"""

from __future__ import annotations

import pytest

from book2audio.attribution import LLMAttributor
from book2audio.attribution.gender import GenderEvidence, GenderVerdict, LLM_CONFIDENCE
from book2audio.attribution.judges import AliasJudge, GenderJudge, JudgeReport
from book2audio.attribution.llm import AttributionCache
from book2audio.attribution.llm_client import LLMError
from book2audio.cast_report import build_cast_report
from book2audio.models import Chapter
from book2audio.text import segment_paragraphs
from book2audio.tts.voices import VoiceCast


class FakeClient:
    """Replays queued replies; an Exception in the queue is raised. Runs ``validate``."""

    temperature = 0.0
    max_tokens = 2048
    requests = 0
    rate_limited = 0

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system, user, validate=None):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if validate is not None:
            validate(reply)
        return reply


def _chapters(*paragraphs: str) -> list[Chapter]:
    return [Chapter(index=1, title="Ch", segments=segment_paragraphs(list(paragraphs)))]


BOOK = _chapters(
    "Mara, the Silver Fox, walked in beside Tom. Under the lamp, the Silver Fox smiled.",
    "Tom said nothing. The weather was cold.",
    "Rin looked tired. She lowered her head and her hands shook.",
    "Pat waited by the door. He and she had met before, and Pat knew it.",
)


def _verdict(name, gender=None, male=0.0, female=0.0):
    return GenderVerdict(
        name=name,
        gender=gender,
        confidence=0.0,
        evidence=(),
        male_weight=male,
        female_weight=female,
    )


# --- alias judge ------------------------------------------------------------------------


def _alias_reply(alias="Silver Fox", name="Mara", evidence="Mara, the Silver Fox, walked in beside Tom."):
    return {"same": [{"alias": alias, "name": name, "evidence": evidence}]}


def _find_aliases(reply, names=("Mara", "Tom", "Silver Fox"), verdicts=None, cache=None):
    report = JudgeReport()
    client = FakeClient(reply)
    found = AliasJudge(client, cache, report).find(list(names), BOOK, verdicts or {})
    return found, report, client


def test_a_passage_naming_both_without_saying_they_are_one_person_is_dropped():
    found, report, _ = _find_aliases(
        _alias_reply(evidence="Mara walked in beside Tom. Under the lamp, the Silver Fox smiled."),
        names=("Mara", "Tom", "Silver Fox"),
    )

    assert found == [] and report.rejected == 1


def test_a_passage_naming_both_is_accepted_when_both_have_the_same_resolved_gender():
    book = _chapters("Mara walked in beside Rin. Under the lamp, Silver Fox smiled.")
    verdicts = {"Mara": _verdict("Mara", "f"), "Silver Fox": _verdict("Silver Fox", "f")}
    client = FakeClient(
        _alias_reply(evidence="Mara walked in beside Rin. Under the lamp, Silver Fox smiled.")
    )

    found = AliasJudge(client, None, JudgeReport()).find(["Mara", "Rin", "Silver Fox"], book, verdicts)

    assert [(f.alias, f.name) for f in found] == [("Silver Fox", "Mara")]


def test_at_most_five_aliases_are_merged_per_book():
    names = [f"Name{i}" for i in range(7)] + [f"Epi{i} Title" for i in range(7)]
    text = " ".join(f"Name{i}, the Epi{i} Title, nodded." for i in range(7))
    book = _chapters(text)
    reply = {
        "same": [
            {
                "alias": f"Epi{i} Title",
                "name": f"Name{i}",
                "evidence": f"Name{i}, the Epi{i} Title, nodded.",
            }
            for i in range(7)
        ]
    }

    found = AliasJudge(FakeClient(reply), None, JudgeReport()).find(names, book, {})

    assert len(found) == 5


def test_a_different_sampling_setting_does_not_reuse_a_cached_answer():
    cache = AttributionCache(None)
    _find_aliases(_alias_reply(), cache=cache)
    other = FakeClient({"same": []})
    other.temperature = 0.7

    AliasJudge(other, cache, JudgeReport()).find(["Mara", "Tom", "Silver Fox"], BOOK, {})

    assert len(other.calls) == 1


def test_an_alias_with_a_verbatim_quote_naming_both_is_accepted():
    found, report, _ = _find_aliases(_alias_reply())

    assert [(f.alias, f.name) for f in found] == [("Silver Fox", "Mara")]
    assert report.rejected == 0


def test_an_alias_whose_quote_is_not_in_the_book_is_dropped():
    found, report, _ = _find_aliases(_alias_reply(evidence="Mara, also called the Silver Fox, grinned."))

    assert found == [] and report.rejected == 1


def test_an_alias_quote_must_name_both_characters():
    found, report, _ = _find_aliases(_alias_reply(evidence="Under the lamp, the Silver Fox smiled."))

    assert found == [] and report.rejected == 1


def test_an_alias_between_two_characters_listed_together_is_dropped():
    book = _chapters("Mara and Tom walked in.")
    report = JudgeReport()
    client = FakeClient(_alias_reply(alias="Tom", name="Mara", evidence="Mara and Tom walked in."))

    found = AliasJudge(client, None, report).find(["Mara", "Tom"], book, {})

    assert found == [] and report.rejected == 1


def test_an_alias_between_characters_of_known_different_gender_is_dropped():
    verdicts = {"Mara": _verdict("Mara", "f"), "Silver Fox": _verdict("Silver Fox", "m")}

    found, report, _ = _find_aliases(_alias_reply(), verdicts=verdicts)

    assert found == [] and report.rejected == 1


def test_an_alias_naming_a_character_who_does_not_speak_is_dropped():
    found, report, _ = _find_aliases(_alias_reply(alias="Silver Fox", name="Ghost"))

    assert found == [] and report.rejected == 1


def test_a_name_is_merged_at_most_once_per_run():
    reply = {
        "same": [
            {"alias": "Silver Fox", "name": "Mara", "evidence": "Mara, the Silver Fox, walked in beside Tom. Under the lamp, the Silver Fox smiled."},
            {"alias": "Tom", "name": "Mara", "evidence": "Mara walked in beside Tom."},
        ]
    }

    found, _, _ = _find_aliases(reply)

    assert [f.alias for f in found] == ["Silver Fox"]


def test_a_reply_of_the_wrong_shape_changes_nothing():
    report = JudgeReport()
    client = FakeClient({"nope": []}, {"nope": []})
    # The fake raises through validate, as LLMClient would after rotating through models.
    with pytest.raises(LLMError):
        client.complete_json("s", "u", validate=lambda r: (_ for _ in ()).throw(LLMError("bad")))

    found = AliasJudge(FakeClient(LLMError("every model failed")), None, report).find(
        ["Mara", "Silver Fox"], BOOK, {}
    )

    assert found == [] and report.failed == 1


def test_an_alias_answer_is_cached_so_the_second_run_makes_no_request():
    cache = AttributionCache(None)
    _find_aliases(_alias_reply(), cache=cache)
    report = JudgeReport()
    client = FakeClient()

    found = AliasJudge(client, cache, report).find(["Mara", "Tom", "Silver Fox"], BOOK, {})

    assert [f.alias for f in found] == ["Silver Fox"]
    assert client.calls == [] and report.cache_hits == 1


def test_no_client_and_no_cache_means_no_findings():
    assert AliasJudge(None, None, JudgeReport()).find(["Mara", "Tom"], BOOK, {}) == []


# --- gender judge -----------------------------------------------------------------------


def _gender_reply(name="Rin", gender="f", quote="Rin looked tired. She lowered her head and her hands shook."):
    return {"verdicts": [{"name": name, "gender": gender, "quote": quote}]}


def _find_genders(reply, unresolved=("Rin",), cache=None):
    report = JudgeReport()
    client = FakeClient(reply)
    found = GenderJudge(client, cache, report).find(list(unresolved), BOOK)
    return found, report, client


def test_a_gender_with_a_verbatim_quote_that_names_the_character_is_accepted():
    found, report, _ = _find_genders(_gender_reply())

    assert [(f.name, f.gender, f.chapter_index) for f in found] == [("Rin", "f", 1)]
    assert report.rejected == 0


def test_the_quote_may_differ_from_the_book_only_in_whitespace_and_quote_style():
    found, _, _ = _find_genders(
        _gender_reply(quote="Rin  looked tired.\nShe lowered her head and her hands shook.")
    )

    assert len(found) == 1


def test_a_gender_quote_that_is_not_in_the_book_is_dropped():
    found, report, _ = _find_genders(_gender_reply(quote="Rin looked tired. She was a queen."))

    assert found == [] and report.rejected == 1


def test_a_gender_quote_that_does_not_name_the_character_is_dropped():
    found, report, _ = _find_genders(_gender_reply(quote="She lowered her head and her hands shook."))

    assert found == [] and report.rejected == 1


def test_a_quote_with_no_word_of_the_claimed_gender_is_dropped():
    found, report, _ = _find_genders(_gender_reply(quote="Pat waited by the door."), unresolved=("Pat",))

    assert found == [] and report.rejected == 1


def test_a_gender_that_contradicts_the_words_of_its_quote_is_dropped():
    found, report, _ = _find_genders(_gender_reply(gender="m"))

    assert found == [] and report.rejected == 1


def test_a_quote_with_words_of_both_genders_proves_nothing():
    found, report, _ = _find_genders(
        _gender_reply(
            name="Pat",
            gender="m",
            quote="Pat waited by the door. He and she had met before, and Pat knew it.",
        ),
        unresolved=("Pat",),
    )

    assert found == [] and report.rejected == 1


def test_unknown_and_unasked_characters_are_ignored():
    reply = {
        "verdicts": [
            {"name": "Rin", "gender": "unknown", "quote": ""},
            {"name": "Stranger", "gender": "f", "quote": "She lowered her head."},
        ]
    }

    found, _, _ = _find_genders(reply)

    assert found == []


def test_the_prompt_carries_only_sentences_that_name_the_character():
    _, _, client = _find_genders(_gender_reply())

    _, user = client.calls[0]
    assert "Rin looked tired." in user and "Tom said nothing" not in user


def test_a_gender_answer_is_cached():
    cache = AttributionCache(None)
    _find_genders(_gender_reply(), cache=cache)
    client = FakeClient()

    found = GenderJudge(client, cache, JudgeReport()).find(["Rin"], BOOK)

    assert len(found) == 1 and client.calls == []


def test_a_failed_request_changes_nothing_and_is_counted():
    report = JudgeReport()

    found = GenderJudge(FakeClient(LLMError("down")), None, report).find(["Rin"], BOOK)

    assert found == [] and report.failed == 1


# --- wired into the attributor ----------------------------------------------------------


STORY = (
    'Mara, the Fox, was tired.',
    '"Move," said Mara.',
    '"No," said Tom.',
    'Under the lamp, the Fox looked up and Mara smiled. Mara raised her hand.',
    '"Fine," said Fox.',
)


def _attribute(judge_reply, *paragraphs, only=None):
    chapter = Chapter(index=1, title="Ch", segments=segment_paragraphs(list(paragraphs)))
    judge = FakeClient(*judge_reply)
    attributor = LLMAttributor(
        "hybrid",
        None,
        AttributionCache(None),
        only_chapters=only,
        judge_client=judge,
    )
    names = attributor.attribute([chapter])
    return attributor, names, judge


def test_a_proven_alias_is_folded_and_the_text_is_relabelled():
    reply = {
        "same": [
            {
                "alias": "Fox",
                "name": "Mara",
                "evidence": "Under the lamp, the Fox looked up and Mara smiled.",
            }
        ]
    }
    # No identity shape offline ("Mara, the Fox"), so the AI has to find it; both are women.
    attributor, names, _ = _attribute(
        [reply, {"verdicts": []}],
        *['Mara raised her hand. She smiled and her eyes were bright.'] * 3,
        *['The Fox raised her hand. She smiled and her eyes were bright.'] * 3,
        '"Move," said Mara.',
        '"No," said Tom.',
        'Under the lamp, the Fox looked up and Mara smiled. Mara raised her hand.',
        '"Fine," said Fox.',
    )

    assert attributor.aliases == {"Fox": "Mara"}
    assert names == ["Mara", "Tom"]
    assert attributor.judge_report.aliases_added == 1


def test_an_unresolved_gender_gets_an_llm_verdict_with_the_quote_as_evidence():
    quote = "Rin looked tired. She lowered her head."
    attributor, _, _ = _attribute(
        [{"same": []}, {"verdicts": [{"name": "Rin", "gender": "f", "quote": quote}]}],
        quote,
        '"Hello," said Rin.',
        '"Hi," said Tom.',
    )

    verdict = attributor.verdicts["Rin"]
    assert attributor.genders["Rin"] == "f"
    assert verdict.confidence == LLM_CONFIDENCE
    assert verdict.evidence[0] == GenderEvidence("llm", "f", verdict.evidence[0].weight, quote, 1)
    assert dict(verdict.layer_counts)["llm"] == 1


def test_a_gender_the_text_already_settled_is_not_asked_about():
    paragraphs = [
        *['Mara raised her hand. She smiled and her eyes were bright.'] * 5,
        '"Move," said Mara.',
        '"No," said Tom.',
    ]
    attributor, _, judge = _attribute([{"same": []}, {"verdicts": []}], *paragraphs)

    assert attributor.genders.get("Mara") == "f"
    gender_prompt = judge.calls[-1][1]
    assert "Mara:" not in gender_prompt and "Tom:" in gender_prompt


def test_the_llm_verdict_is_dropped_when_the_texts_own_pronouns_say_the_opposite():
    quote = "Rin looked tired. She lowered her head."
    paragraphs = [
        *['Rin said it. He nodded.', 'Rin sat. His hands shook, and he sighed.'] * 3,
        quote,
        '"Hello," said Rin.',
        '"Hi," said Tom.',
    ]
    attributor, _, _ = _attribute(
        [{"same": []}, {"verdicts": [{"name": "Rin", "gender": "f", "quote": quote}]}], *paragraphs
    )

    assert attributor.genders.get("Rin") != "f"
    assert attributor.judge_report.rejected == 1


def test_a_failing_judge_leaves_the_offline_result_alone():
    attributor, names, _ = _attribute(
        [LLMError("down"), LLMError("down")],
        '"Hello," said Rin.',
        '"Hi," said Tom.',
    )

    assert names == ["Rin", "Tom"] and attributor.genders == {}
    assert attributor.judge_report.failed >= 1


def test_the_llm_evidence_shows_up_in_the_cast_report():
    quote = "Rin looked tired. She lowered her head."
    chapter = Chapter(
        index=1,
        title="Ch",
        segments=segment_paragraphs([quote, '"Hello," said Rin.', '"Hi," said Tom.']),
    )
    attributor = LLMAttributor(
        "hybrid",
        None,
        AttributionCache(None),
        judge_client=FakeClient(
            {"same": []}, {"verdicts": [{"name": "Rin", "gender": "f", "quote": quote}]}
        ),
    )
    names = attributor.attribute([chapter])
    cast = VoiceCast()
    cast.cast(names, attributor.genders)

    entries = build_cast_report(names, attributor.verdicts, [chapter], cast)

    rin = next(e for e in entries if e.name == "Rin")
    assert rin.gender == "f" and rin.needs_review  # 0.7 is below the review line
    assert rin.layers["llm"] == 1
    assert rin.evidence[0]["layer"] == "llm" and rin.evidence[0]["snippet"] == quote


def test_the_alias_prompt_offers_runs_of_sentences_that_name_two_characters():
    # The nickname and the name sit in neighbouring sentences, not the same one.
    _, _, client = _find_aliases({"same": []})

    _, user = client.calls[0]
    passages = user.split("Passages naming more than one of them:")[1]
    assert "Mara, the Silver Fox, walked in beside Tom. Under the lamp, the Silver Fox smiled." in passages


def test_an_alias_quote_may_span_sentences():
    found, _, _ = _find_aliases(
        _alias_reply(evidence="Mara, the Silver Fox, walked in beside Tom. Under the lamp, the Silver Fox smiled.")
    )

    assert len(found) == 1


# --- reasons, ordering, clauses -----------------------------------------------------------


def test_every_dropped_answer_is_recorded_with_the_name_and_the_reason():
    found, report, _ = _find_genders(_gender_reply(quote="Rin looked tired. She was a queen."))

    assert found == []
    assert report.reasons == ["gender Rin: quote is not in the book"]


def test_a_dropped_answer_is_logged_without_book_text(caplog):
    with caplog.at_level("INFO", logger="book2audio.attribution.judges"):
        _find_genders(_gender_reply(quote="Rin looked tired. She was a queen."))

    assert "Rin" in caplog.text and "not in the book" in caplog.text
    assert "queen" not in caplog.text


def test_the_reasons_cover_the_guards_that_fire():
    reasons = []
    for quote, gender in (
        ("She lowered her head and her hands shook.", "f"),  # does not name Rin
        ("Pat waited by the door.", "f"),  # not Rin's sentence, no gender word
        ("Rin looked tired. She lowered her head and her hands shook.", "m"),  # contradicts
    ):
        _, report, _ = _find_genders(_gender_reply(gender=gender, quote=quote))
        reasons += report.reasons

    assert reasons == [
        "gender Rin: quote does not name the character",
        "gender Rin: quote does not name the character",
        "gender Rin: quote's gender words contradict the verdict",
    ]


def test_an_unknown_verdict_is_recorded_as_such():
    _, report, _ = _find_genders({"verdicts": [{"name": "Rin", "gender": "unknown", "quote": ""}]})

    assert report.reasons == ["gender Rin: the model could not tell"]


def test_a_single_clause_of_a_mixed_sentence_can_be_the_quote():
    book = _chapters("Pat waited by the door while she watched and he sighed.")
    clause = "Pat waited by the door while she watched"
    client = FakeClient(_gender_reply(name="Pat", gender="f", quote=clause))

    found = GenderJudge(client, None, JudgeReport()).find(["Pat"], book)

    assert [(f.name, f.gender) for f in found] == [("Pat", "f")]


def test_sentences_with_one_genders_words_come_before_mixed_ones():
    book = _chapters(
        "Pat knew that he and she had met.",
        "Pat nodded.",
        "Pat said her name was Pat.",
    )

    client = FakeClient({"verdicts": []})
    GenderJudge(client, None, JudgeReport()).find(["Pat"], book)

    shown = client.calls[0][1].splitlines()[2:]
    assert shown == [
        "  - Pat said her name was Pat.",
        "  - Pat nodded.",
        "  - Pat knew that he and she had met.",
    ]


def test_the_busiest_unresolved_characters_are_judged_when_there_are_too_many():
    from book2audio.attribution import judges

    # Twelve quiet characters first in reading order, then one with many lines.
    quiet = [f'"Hi," said Quiet{i}. Quiet{i} sat down.' for i in range(judges.MAX_JUDGED_CHARACTERS)]
    loud = ['"Hello," said Busy.', 'Busy sat.'] * 4
    chapter = Chapter(index=1, title="Ch", segments=segment_paragraphs([*quiet, *loud]))
    judge = FakeClient({"same": []}, {"verdicts": []})
    attributor = LLMAttributor("hybrid", None, AttributionCache(None), judge_client=judge)

    attributor.attribute([chapter])

    gender_prompt = judge.calls[-1][1]
    assert "Busy:" in gender_prompt


def test_the_adjacent_epithet_shape_needs_a_multi_word_epithet():
    book = _chapters("Fox Mara walked in.")
    reply = _alias_reply(alias="Fox", name="Mara", evidence="Fox Mara walked in.")

    found = AliasJudge(FakeClient(reply), None, JudgeReport()).find(["Mara", "Fox"], book, {})

    assert found == []


def test_a_model_supplied_name_cannot_forge_a_log_line(caplog):
    reply = {"verdicts": [{"name": "Ghost\nERROR forged line\r\x1b[31m", "gender": "f", "quote": "x"}]}

    with caplog.at_level("INFO", logger="book2audio.attribution.judges"):
        _, report, _ = _find_genders(reply)

    assert all("\n" not in r.getMessage() and "\r" not in r.getMessage() for r in caplog.records)
    assert "\x1b" not in caplog.text
    assert report.reasons == ["gender Ghost ERROR forged line [31m: not a character that was asked about"]


def test_a_name_with_nothing_printable_is_logged_as_a_question_mark():
    report = JudgeReport()

    report.reject("gender", "\n\x00", "because")

    assert report.reasons == ["gender ?: because"]
