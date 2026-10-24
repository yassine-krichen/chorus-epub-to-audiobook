"""Tests for the LLM speaker attributor and its chat client.

``httpx.post`` is mocked at ``book2audio.attribution.llm_client`` (same style as
test_freellmapi.py) -- no real network call or credential is used.
"""

from __future__ import annotations

import json
import os
import re
from unittest.mock import MagicMock

import httpx
import pytest

from book2audio.attribution import llm, llm_client
from book2audio.attribution.llm import AttributionCache, LLMAttributor
from book2audio.attribution.llm_client import (
    LLMClient,
    LLMError,
    parse_json_reply,
    require_llm_attribution_configured,
)
from book2audio.models import NARRATOR, Chapter, Segment, SegmentKind


# A recognizable fake key: tests assert it never shows up in any message.
API_KEY = "sk-test-SECRET-9f3a"


@pytest.fixture(autouse=True)
def _llm_env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com/v1")
    monkeypatch.setenv("FREELLMAPI_API_KEY", API_KEY)
    monkeypatch.setenv("FREELLMAPI_MODEL", "test-model")


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    return sleeps


def _dlg(text):
    return Segment(kind=SegmentKind.DIALOGUE, text=text)


def _nar(text):
    return Segment(kind=SegmentKind.NARRATION, text=text)


def _break():
    return Segment(kind=SegmentKind.SCENE_BREAK, text="")


def _chapter(segments, index=1):
    return Chapter(index=index, title="Ch", segments=segments)


def _named_chapter():
    # Both lines carry an explicit trailing name: nothing for hybrid to ask about.
    return _chapter(
        [_dlg("Who are you?"), _nar("asked Sunny."), _dlg("A friend."), _nar("replied Nephis.")]
    )


def _alternation_chapter():
    # The last line has no tag, so the heuristic alternates back to Sunny.
    return _chapter(
        [
            _dlg("Who are you?"),
            _nar("asked Sunny."),
            _dlg("A friend."),
            _nar("replied Nephis."),
            _dlg("Prove it."),
        ]
    )


def _untagged_scenes(scenes):
    """One untagged dialogue line per scene: each is its own window, and the
    heuristic can only guess (fallback tier), so hybrid targets every one."""
    segments = []
    for n in range(scenes):
        if n:
            segments.append(_break())
        segments += [_nar("Sunny waited."), _dlg(f"Line {n}.")]
    return _chapter(segments)


def _chat_body(content):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def _labels(*pairs):
    return json.dumps({"labels": [{"id": i, "speaker": s} for i, s in pairs]})


def _prompt(kwargs):
    return kwargs["json"]["messages"][1]["content"]


def _target_ids(kwargs):
    line = re.search(r"Label dialogue ids: (.*)", _prompt(kwargs)).group(1)
    return [int(x) for x in line.split(",")]


def _echo_post(speaker):
    """A fake httpx.post that labels every requested id with ``speaker``."""

    def post(url, **kwargs):
        return _chat_body(_labels(*[(i, speaker) for i in _target_ids(kwargs)]))

    return MagicMock(side_effect=post)


def _attributor(mode="hybrid", budget=150, cache=None):
    return LLMAttributor(mode, LLMClient(), cache or AttributionCache(None), budget)


def _install(monkeypatch, post):
    monkeypatch.setattr(llm_client.httpx, "post", post)
    return post


def _http_error(status=429, text="prompt too large for the model"):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=response
    )
    return response


# --- hybrid vs llm targeting ------------------------------------------------


def test_hybrid_makes_no_request_when_every_line_is_named(monkeypatch):
    post = _install(monkeypatch, MagicMock())
    chapter = _named_chapter()

    attributor = _attributor("hybrid")
    names = attributor.attribute([chapter])

    post.assert_not_called()
    assert names == ["Sunny", "Nephis"]
    assert attributor.report.calls == 0
    assert attributor.report.windows_skipped == 1


def test_hybrid_overrides_an_alternation_line(monkeypatch):
    post = _install(monkeypatch, _echo_post("Nephis"))
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Nephis"
    assert _target_ids(post.call_args.kwargs) == [5]
    assert attributor.report.lines_changed == 1
    assert attributor.report.lines_targeted == 1


def test_hybrid_leaves_named_lines_untouched_even_if_the_model_disagrees(monkeypatch):
    post = _install(
        monkeypatch, MagicMock(return_value=_chat_body(_labels((1, "Nephis"), (5, "Nephis"))))
    )
    chapter = _alternation_chapter()

    _attributor("hybrid").attribute([chapter])

    assert post.call_count == 1
    assert chapter.segments[0].speaker == "Sunny"  # id 1 was not a target
    assert chapter.segments[4].speaker == "Nephis"


def test_llm_mode_targets_every_dialogue_line(monkeypatch):
    post = _install(monkeypatch, _echo_post("Sunny"))
    chapter = _alternation_chapter()

    attributor = _attributor("llm")
    attributor.attribute([chapter])

    assert _target_ids(post.call_args.kwargs) == [1, 3, 5]
    dialogue = [s for s in chapter.segments if s.kind == SegmentKind.DIALOGUE]
    assert [s.speaker for s in dialogue] == ["Sunny", "Sunny", "Sunny"]
    assert attributor.report.lines_targeted == 3


def test_attributor_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        LLMAttributor("heuristic", LLMClient())


# --- cache ------------------------------------------------------------------


def test_a_warm_cache_makes_zero_requests(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    post = _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])
    assert post.call_count == 1

    chapter = _alternation_chapter()
    warm = _attributor("hybrid", cache=AttributionCache(cache_path))
    warm.attribute([chapter])

    assert post.call_count == 1
    assert warm.report.cache_hits == 1
    assert warm.report.calls == 0
    assert chapter.segments[4].speaker == "Nephis"


def test_a_cache_hit_does_not_count_against_the_budget(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    warm = _attributor("hybrid", budget=0, cache=AttributionCache(cache_path))
    warm.attribute([_alternation_chapter()])

    assert warm.report.cache_hits == 1
    assert warm.report.budget_exhausted is False


def test_the_cache_is_invalidated_when_the_window_text_changes(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    post = _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    edited = _alternation_chapter()
    edited.segments[4].text = "Prove it, then."
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([edited])

    assert post.call_count == 2


def test_the_cache_is_invalidated_when_the_model_changes(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    post = _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    monkeypatch.setenv("FREELLMAPI_MODEL", "another-model")
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    assert post.call_count == 2


def test_the_cache_file_is_written_atomically_through_a_unique_temp_file(monkeypatch, tmp_path):
    cache_path = tmp_path / "attribution-cache.json"
    replaced = []
    real_replace = llm.os.replace

    def recording_replace(src, dst):
        replaced.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(llm.os, "replace", recording_replace)
    _install(monkeypatch, _echo_post("Nephis"))

    _attributor("hybrid", cache=AttributionCache(str(cache_path))).attribute(
        [_alternation_chapter()]
    )

    assert len(replaced) == 1
    src, dst = replaced[0]
    assert dst == str(cache_path)
    assert src != dst and src.endswith(".tmp") and str(os.getpid()) in src
    assert len(json.loads(cache_path.read_text(encoding="utf-8"))["entries"]) == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_two_cache_writes_use_different_temp_files(monkeypatch, tmp_path):
    sources = []
    real_replace = llm.os.replace
    monkeypatch.setattr(
        llm.os, "replace", lambda src, dst: (sources.append(src), real_replace(src, dst))[1]
    )
    cache = AttributionCache(str(tmp_path / "attribution-cache.json"))

    cache.put("a", [{"id": 1, "speaker": "X"}])
    cache.put("b", [{"id": 1, "speaker": "Y"}])

    assert len(set(sources)) == 2


def test_a_failed_cache_write_warns_cleans_up_and_does_not_fail_attribution(
    monkeypatch, tmp_path
):
    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(llm.os, "replace", broken_replace)
    _install(monkeypatch, _echo_post("Nephis"))
    chapter = _alternation_chapter()

    attributor = _attributor(
        "hybrid", cache=AttributionCache(str(tmp_path / "attribution-cache.json"))
    )
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Nephis"
    assert any("attribution cache" in w for w in attributor.report.warnings)
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_corrupt_cache_file_is_treated_as_empty(tmp_path):
    cache_path = tmp_path / "attribution-cache.json"
    cache_path.write_text("{not json", encoding="utf-8")

    assert AttributionCache(str(cache_path)).get("anything") is None


# --- failure handling --------------------------------------------------------


def test_an_http_error_falls_back_to_the_heuristic(monkeypatch):
    post = _install(monkeypatch, MagicMock(return_value=_http_error()))
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])  # must not raise

    assert chapter.segments[4].speaker == "Sunny"
    assert attributor.report.windows_failed == 1
    assert attributor.report.calls == 1
    assert post.call_count == 2  # one retry inside the client


def test_malformed_json_falls_back_to_the_heuristic(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_chat_body("sorry, I cannot do that")))
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"
    assert attributor.report.windows_failed == 1


def test_a_reply_without_a_labels_list_falls_back_to_the_heuristic(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_chat_body('{"answer": "Nephis"}')))
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"
    assert attributor.report.windows_failed == 1


def test_failed_windows_are_not_cached(monkeypatch, tmp_path):
    cache_path = tmp_path / "attribution-cache.json"
    _install(monkeypatch, MagicMock(return_value=_chat_body("nope")))

    _attributor("hybrid", cache=AttributionCache(str(cache_path))).attribute(
        [_alternation_chapter()]
    )

    assert not cache_path.exists()


def test_a_new_name_that_is_not_in_the_window_text_is_rejected(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "Zebulon")))))
    chapter = _alternation_chapter()

    _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"


def _chapter_mentioning_cassie():
    chapter = _alternation_chapter()
    chapter.segments.append(_nar("Cassie stepped closer."))
    return chapter


def test_a_new_name_that_appears_capitalized_in_the_window_is_accepted(monkeypatch):
    chapter = _chapter_mentioning_cassie()
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "Cassie")))))

    names = _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == "Cassie"
    assert "Cassie" in names


def test_a_new_name_only_in_lowercase_form_is_rejected(monkeypatch):
    chapter = _chapter_mentioning_cassie()
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "cassie")))))

    _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"


def test_known_names_match_case_insensitively(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "nephis")))))
    chapter = _alternation_chapter()

    _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == "Nephis"


def test_a_pronoun_is_not_accepted_as_a_new_name(monkeypatch):
    chapter = _alternation_chapter()
    chapter.segments.append(_nar("He shrugged."))
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "He")))))

    _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"


def test_out_of_window_ids_are_ignored(monkeypatch):
    _install(
        monkeypatch,
        MagicMock(return_value=_chat_body(_labels((99, "Nephis"), (0, "Nephis"), (-3, "Nephis")))),
    )
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"
    assert attributor.report.lines_changed == 0


def test_malformed_label_items_are_skipped_without_failing_the_window(monkeypatch):
    body = json.dumps({"labels": ["x", {"id": "5", "speaker": "Nephis"}, {"id": 5, "speaker": 7}]})
    _install(monkeypatch, MagicMock(return_value=_chat_body(body)))
    chapter = _alternation_chapter()

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"
    assert attributor.report.windows_failed == 0


def test_narrator_is_an_accepted_label(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "narrator")))))
    chapter = _alternation_chapter()

    _attributor("hybrid").attribute([chapter])

    assert chapter.segments[4].speaker == NARRATOR


# --- budget and circuit breaker ---------------------------------------------


def test_budget_exhaustion_keeps_heuristic_labels_for_the_remaining_windows(monkeypatch):
    post = _install(monkeypatch, _echo_post("Sunny"))
    chapter = _untagged_scenes(4)

    attributor = _attributor("llm", budget=2)
    attributor.attribute([chapter])

    assert post.call_count == 2
    report = attributor.report
    assert report.calls == 2
    assert report.budget_exhausted is True
    assert report.windows_unprocessed == 2
    assert any("budget" in w for w in report.warnings)
    dialogue = [s for s in chapter.segments if s.kind == SegmentKind.DIALOGUE]
    assert [s.speaker for s in dialogue[:2]] == ["Sunny", "Sunny"]
    assert [s.speaker for s in dialogue[2:]] == [NARRATOR, NARRATOR]  # heuristic fallback


def test_three_consecutive_failures_stop_further_requests(monkeypatch):
    post = _install(monkeypatch, MagicMock(return_value=_http_error(500, "boom")))
    chapter = _untagged_scenes(6)

    attributor = _attributor("llm")
    attributor.attribute([chapter])  # must not raise

    assert post.call_count == 3 * 2  # three windows, one client retry each
    report = attributor.report
    assert report.breaker_tripped is True
    assert report.windows_failed == 3
    assert report.windows_unprocessed == 3
    assert any("consecutive" in w for w in report.warnings)


def test_a_success_resets_the_consecutive_failure_count(monkeypatch):
    failure, success = _http_error(500, "x"), _chat_body(_labels((2, "Nephis")))
    # Each failing window costs two attempts (one client retry); a success costs one.
    sequence = [failure] * 4 + [success] + [failure] * 4 + [success]
    post = _install(monkeypatch, MagicMock(side_effect=sequence))
    chapter = _untagged_scenes(6)

    attributor = _attributor("llm")
    attributor.attribute([chapter])

    assert attributor.report.breaker_tripped is False
    assert attributor.report.calls == 6
    assert post.call_count == len(sequence)


# --- request shape -----------------------------------------------------------


def test_the_url_has_no_duplicated_v1(monkeypatch):
    post = _install(monkeypatch, _echo_post("Nephis"))

    _attributor("hybrid").attribute([_alternation_chapter()])

    assert post.call_args.args[0] == "https://freellmapi.example.com/v1/chat/completions"


def test_a_trailing_slash_on_the_base_url_is_tolerated(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com/v1/")
    post = _install(monkeypatch, _echo_post("Nephis"))

    _attributor("hybrid").attribute([_alternation_chapter()])

    assert post.call_args.args[0] == "https://freellmapi.example.com/v1/chat/completions"


def test_the_request_uses_temperature_zero_the_configured_model_and_a_token_cap(monkeypatch):
    post = _install(monkeypatch, _echo_post("Nephis"))

    _attributor("hybrid").attribute([_alternation_chapter()])

    kwargs = post.call_args.kwargs
    assert kwargs["json"]["model"] == "test-model"
    assert kwargs["json"]["temperature"] == 0
    assert kwargs["json"]["max_tokens"] > 0
    assert "response_format" not in kwargs["json"]
    assert kwargs["headers"] == {"Authorization": f"Bearer {API_KEY}"}
    assert kwargs["timeout"] == 120.0


def test_the_model_defaults_to_auto_when_unset(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_MODEL")

    assert LLMClient().model == "auto"


def test_the_prompt_lists_known_characters_and_marks_attributed_lines(monkeypatch):
    post = _install(monkeypatch, _echo_post("Nephis"))

    _attributor("hybrid").attribute([_alternation_chapter()])

    prompt = _prompt(post.call_args.kwargs)
    assert "Known characters: Sunny; Nephis" in prompt
    assert "[5] DIALOGUE:" in prompt
    assert "already attributed to Sunny" in prompt


def test_context_is_limited_to_three_segments_each_side_within_the_scene(monkeypatch):
    segments = [_dlg(f"Line {n:02d} here.") for n in range(30)] + [_break(), _nar("Other scene.")]
    post = _install(monkeypatch, _echo_post("Sunny"))

    _attributor("llm").attribute([_chapter(segments)])

    first, second = (_prompt(c.kwargs) for c in post.call_args_list)
    # Window 1 holds lines 00-24; lines 25-27 are read-only context after it.
    assert "Line 27 here." in first and "Line 28 here." not in first
    # Window 2 holds lines 25-29; lines 22-24 are context before it, and the
    # scene break keeps the next scene out.
    assert "Line 22 here." in second and "Line 21 here." not in second
    assert "Other scene." not in second


# --- windows -----------------------------------------------------------------


def test_a_scene_break_starts_a_new_window(monkeypatch):
    chapter = _chapter(
        [_nar("One."), _dlg("First scene."), _break(), _nar("Two."), _dlg("Second scene.")]
    )
    post = _install(monkeypatch, _echo_post("Sunny"))

    _attributor("llm").attribute([chapter])

    prompts = [_prompt(c.kwargs) for c in post.call_args_list]
    assert len(prompts) == 2
    assert "First scene." in prompts[0] and "Second scene." not in prompts[0]
    assert "Second scene." in prompts[1] and "First scene." not in prompts[1]


def test_long_runs_of_dialogue_split_into_windows_of_at_most_25_lines(monkeypatch):
    segments = []
    for n in range(60):
        segments += [_dlg(f"Line {n}."), _nar("said Sunny.")]
    post = _install(monkeypatch, _echo_post("Sunny"))

    _attributor("llm").attribute([_chapter(segments)])

    assert post.call_count == 3
    assert all(len(_target_ids(c.kwargs)) <= 25 for c in post.call_args_list)


def test_a_long_scene_splits_by_character_count(monkeypatch):
    segments = [_dlg("x" * 2500), _dlg("y" * 2500), _dlg("z" * 2500)]
    post = _install(monkeypatch, _echo_post("Sunny"))

    _attributor("llm").attribute([_chapter(segments)])

    assert post.call_count == 2


def test_a_hybrid_window_with_no_unsure_lines_is_skipped(monkeypatch):
    post = _install(monkeypatch, _echo_post("Nephis"))
    chapter = _chapter([_dlg("Who?"), _nar("asked Sunny."), _break(), _nar("Later."), _dlg("Well.")])

    attributor = _attributor("hybrid")
    attributor.attribute([chapter])

    assert post.call_count == 1  # only the second scene has a guessed line
    assert attributor.report.windows_skipped == 1


def test_only_chapters_limits_requests_to_the_selected_chapters(monkeypatch):
    post = _install(monkeypatch, _echo_post("Sunny"))
    chapters = [_untagged_scenes(1), _untagged_scenes(1)]
    chapters[1].index = 2

    attributor = LLMAttributor(
        "llm", LLMClient(), AttributionCache(None), 150, only_chapters=frozenset({2})
    )
    attributor.attribute(chapters)

    assert post.call_count == 1


def test_estimate_counts_windows_without_any_request(monkeypatch):
    post = _install(monkeypatch, MagicMock())
    attributor = _attributor("llm")

    attributor.estimate([_untagged_scenes(3)])

    post.assert_not_called()
    assert attributor.report.windows == 3
    assert attributor.report.lines_targeted == 3


# --- genders and names -------------------------------------------------------


def _pronoun_chapter():
    return _chapter(
        [
            _dlg("Hello."),
            _nar("said Sunny."),
            _dlg("Hi."),
            _nar("said Nephis."),
            _dlg("Shall we?"),
            _nar("She said, quietly."),
        ]
    )


def test_genders_and_names_are_recomputed_from_the_final_speakers(monkeypatch):
    # Baseline: the heuristic resolves the pronoun tag to the previous speaker,
    # Nephis, so the "she" vote lands on her.
    baseline = _pronoun_chapter()
    heuristic_only = LLMAttributor("hybrid", None)
    heuristic_only.attribute([baseline])
    assert baseline.segments[4].speaker == "Nephis"
    assert heuristic_only.genders == {"Nephis": "f"}

    chapter = _pronoun_chapter()
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "Sunny")))))
    attributor = _attributor("hybrid")
    names = attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Sunny"
    assert names == ["Sunny", "Nephis"]
    assert attributor.genders == {"Sunny": "f"}


def test_a_name_with_no_remaining_lines_is_dropped_from_the_returned_names(monkeypatch):
    chapter = _chapter([_dlg("Hello."), _nar("said Sunny."), _dlg("Hm.")])
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((3, "Narrator")))))

    names = _attributor("llm").attribute([chapter])

    assert names == ["Sunny"]


# --- report ------------------------------------------------------------------


def test_the_report_summary_is_a_single_line(monkeypatch):
    _install(monkeypatch, _echo_post("Nephis"))
    attributor = _attributor("hybrid")
    attributor.attribute([_alternation_chapter()])

    summary = attributor.report.summary()

    assert "\n" not in summary
    assert "1 call(s)" in summary and "budget exhausted: no" in summary


# --- client ------------------------------------------------------------------


def test_json_in_a_code_fence_is_parsed():
    assert parse_json_reply('```json\n{"labels": []}\n```') == {"labels": []}


def test_json_surrounded_by_prose_is_parsed():
    assert parse_json_reply('Sure! {"labels": []} Hope that helps.') == {"labels": []}


def test_unparseable_text_raises_llm_error():
    with pytest.raises(LLMError):
        parse_json_reply("no json here")


def test_client_normalizes_a_transport_error_to_llm_error(monkeypatch):
    post = _install(monkeypatch, MagicMock(side_effect=httpx.ConnectError("boom")))

    with pytest.raises(LLMError):
        LLMClient().complete_json("sys", "user")

    assert post.call_count == 2


def test_client_retries_once_after_a_transient_failure_then_succeeds(monkeypatch):
    post = _install(
        monkeypatch,
        MagicMock(side_effect=[httpx.ConnectError("boom"), _chat_body('{"labels": []}')]),
    )

    assert LLMClient().complete_json("sys", "user") == {"labels": []}
    assert post.call_count == 2


def test_client_raises_llm_error_when_the_response_shape_is_wrong(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": []}
    _install(monkeypatch, MagicMock(return_value=response))

    with pytest.raises(LLMError):
        LLMClient().complete_json("sys", "user")


def test_http_error_message_includes_the_status_and_body_but_never_the_key(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_http_error(429, "prompt too large")))

    with pytest.raises(LLMError) as excinfo:
        LLMClient().complete_json("sys", "user")

    assert "429" in str(excinfo.value) and "prompt too large" in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


def test_missing_credentials_at_call_time_raise_llm_error(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_API_KEY")

    with pytest.raises(LLMError, match="FREELLMAPI_API_KEY"):
        LLMClient().complete_json("sys", "user")


def test_require_llm_attribution_configured_passes_when_env_is_set():
    require_llm_attribution_configured()


@pytest.mark.parametrize("missing", ["FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY"])
def test_require_llm_attribution_configured_names_the_missing_variable(monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match=missing):
        require_llm_attribution_configured()


def test_require_llm_attribution_configured_does_not_echo_values(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_API_KEY")
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://very-secret-host.example/v1")

    with pytest.raises(RuntimeError) as excinfo:
        require_llm_attribution_configured()
    assert "very-secret-host" not in str(excinfo.value)

    monkeypatch.setenv("FREELLMAPI_API_KEY", API_KEY)
    monkeypatch.delenv("FREELLMAPI_BASE_URL")
    with pytest.raises(RuntimeError) as excinfo:
        require_llm_attribution_configured()
    assert API_KEY not in str(excinfo.value)


# --- cache keys and what gets cached ---------------------------------------------


def test_the_cache_is_invalidated_when_the_system_prompt_changes(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    post = _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    monkeypatch.setattr(llm, "SYSTEM_PROMPT", llm.SYSTEM_PROMPT + " Be careful.")
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    assert post.call_count == 2


def test_the_cache_is_invalidated_when_max_tokens_changes(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "attribution-cache.json")
    post = _install(monkeypatch, _echo_post("Nephis"))
    _attributor("hybrid", cache=AttributionCache(cache_path)).attribute([_alternation_chapter()])

    other = LLMAttributor("hybrid", LLMClient(max_tokens=100), AttributionCache(cache_path))
    other.attribute([_alternation_chapter()])

    assert post.call_count == 2


def test_an_empty_reply_is_not_cached(monkeypatch, tmp_path):
    cache_path = tmp_path / "attribution-cache.json"
    _install(monkeypatch, MagicMock(return_value=_chat_body('{"labels": []}')))

    _attributor("hybrid", cache=AttributionCache(str(cache_path))).attribute(
        [_alternation_chapter()]
    )

    assert not cache_path.exists()


def test_a_fully_rejected_reply_is_not_cached(monkeypatch, tmp_path):
    cache_path = tmp_path / "attribution-cache.json"
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "Zebulon"), (99, "X")))))

    _attributor("hybrid", cache=AttributionCache(str(cache_path))).attribute(
        [_alternation_chapter()]
    )

    assert not cache_path.exists()


# --- retry policy ----------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(monkeypatch, _no_backoff_sleep, status):
    post = _install(monkeypatch, MagicMock(return_value=_http_error(status, "nope")))

    with pytest.raises(LLMError, match=str(status)):
        LLMClient().complete_json("sys", "user")

    assert post.call_count == 1
    assert _no_backoff_sleep == []


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_rate_limits_and_server_errors_are_retried_once_with_a_backoff(
    monkeypatch, _no_backoff_sleep, status
):
    post = _install(
        monkeypatch,
        MagicMock(side_effect=[_http_error(status, "busy"), _chat_body('{"labels": []}')]),
    )

    assert LLMClient().complete_json("sys", "user") == {"labels": []}
    assert post.call_count == 2
    assert len(_no_backoff_sleep) == 1 and 0 < _no_backoff_sleep[0] <= 5


def test_timeouts_are_retried(monkeypatch):
    post = _install(
        monkeypatch,
        MagicMock(side_effect=[httpx.ReadTimeout("slow"), _chat_body('{"labels": []}')]),
    )

    assert LLMClient().complete_json("sys", "user") == {"labels": []}
    assert post.call_count == 2


def test_an_unparseable_reply_is_not_retried(monkeypatch):
    post = _install(monkeypatch, MagicMock(return_value=_chat_body("not json")))

    with pytest.raises(LLMError):
        LLMClient().complete_json("sys", "user")

    assert post.call_count == 1


def test_the_default_timeout_allows_slow_routed_models():
    assert LLMClient().timeout == 120.0


# --- progress callback -------------------------------------------------------------


def test_progress_is_reported_after_every_window_including_cached_and_failed(
    monkeypatch, tmp_path
):
    cache_path = str(tmp_path / "attribution-cache.json")
    ticks = MagicMock()
    _install(monkeypatch, _echo_post("Sunny"))
    first = LLMAttributor(
        "llm", LLMClient(), AttributionCache(cache_path), 150, on_progress=ticks
    )
    first.attribute([_untagged_scenes(3)])
    assert ticks.call_count == 3

    ticks.reset_mock()
    warm = LLMAttributor("llm", LLMClient(), AttributionCache(cache_path), 150, on_progress=ticks)
    warm.attribute([_untagged_scenes(3)])
    assert warm.report.cache_hits == 3 and ticks.call_count == 3

    ticks.reset_mock()
    _install(monkeypatch, MagicMock(return_value=_http_error(500, "x")))
    failing = LLMAttributor("llm", LLMClient(), AttributionCache(None), 150, on_progress=ticks)
    failing.attribute([_untagged_scenes(5)])
    assert ticks.call_count == 5  # also for windows skipped by the open circuit breaker


def test_estimate_does_not_report_progress(monkeypatch):
    ticks = MagicMock()
    attributor = LLMAttributor("llm", LLMClient(), None, 150, on_progress=ticks)

    attributor.estimate([_untagged_scenes(2)])

    ticks.assert_not_called()


# --- unexpected errors on the LLM path ----------------------------------------------


class _ExplodingClient:
    model = "m"
    temperature = 0.0
    max_tokens = 10

    def complete_json(self, system, user):
        raise RuntimeError("boom with " + API_KEY)


def test_an_unexpected_exception_in_the_llm_path_still_falls_back_to_the_heuristic():
    chapter = _alternation_chapter()

    attributor = LLMAttributor("hybrid", _ExplodingClient(), AttributionCache(None))
    names = attributor.attribute([chapter])  # must not raise

    assert chapter.segments[4].speaker == "Sunny"
    assert names == ["Sunny", "Nephis"]
    assert attributor.report.windows_failed == 1
    assert any("unexpected error" in w for w in attributor.report.warnings)
    # Only the exception type is reported: its message may carry secrets.
    assert API_KEY not in " ".join(attributor.report.warnings)


def test_repeated_unexpected_exceptions_trip_the_circuit_breaker():
    attributor = LLMAttributor("llm", _ExplodingClient(), AttributionCache(None))

    attributor.attribute([_untagged_scenes(6)])

    assert attributor.report.breaker_tripped is True
    assert attributor.report.calls == 3


def test_a_progress_callback_error_is_not_swallowed_as_an_llm_failure(monkeypatch):
    _install(monkeypatch, _echo_post("Sunny"))

    def broken():
        raise ValueError("heartbeat store down")

    attributor = LLMAttributor("llm", LLMClient(), AttributionCache(None), 150, on_progress=broken)

    with pytest.raises(ValueError, match="heartbeat store down"):
        attributor.attribute([_untagged_scenes(1)])


# --- name handling after the model answers ---------------------------------------------


def test_a_nickname_the_model_introduces_is_folded_into_the_full_name(monkeypatch):
    chapter = _alternation_chapter()
    chapter.segments.append(_nar("Neph grinned."))
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((5, "Neph")))))

    attributor = _attributor("hybrid")
    names = attributor.attribute([chapter])

    assert chapter.segments[4].speaker == "Nephis"
    assert names == ["Sunny", "Nephis"]
    assert attributor.report.lines_changed == 1


def test_a_name_with_non_ascii_letters_is_accepted_when_present_in_the_text(monkeypatch):
    chapter = _chapter([_nar("Zoë stepped closer."), _dlg("Well.")])
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((2, "Zoë")))))

    _attributor("llm").attribute([chapter])

    assert chapter.segments[1].speaker == "Zoë"


def test_a_capitalized_common_word_is_rejected_as_a_name(monkeypatch):
    chapter = _chapter([_nar("The garden was quiet. Garden walls were high."), _dlg("Well.")])
    _install(monkeypatch, MagicMock(return_value=_chat_body(_labels((2, "Garden")))))

    _attributor("llm").attribute([chapter])

    assert chapter.segments[1].speaker == NARRATOR


def test_a_new_name_must_be_present_in_each_window_it_is_used_in(monkeypatch):
    chapter = _chapter(
        [
            _nar("Cassie waited."),
            _dlg("First."),
            _break(),
            _nar("Rain fell."),
            _dlg("Second."),
        ]
    )
    _install(monkeypatch, _echo_post("Cassie"))

    _attributor("llm").attribute([chapter])

    assert chapter.segments[1].speaker == "Cassie"
    assert chapter.segments[4].speaker == NARRATOR  # "Cassie" is not in scene two


# --- the key never leaks -----------------------------------------------------------------


def test_the_api_key_is_absent_from_every_error_and_report_message(monkeypatch):
    _install(monkeypatch, MagicMock(return_value=_http_error(401, "bad credentials")))
    attributor = _attributor("llm")

    attributor.attribute([_untagged_scenes(4)])

    report = attributor.report
    text = " ".join([report.summary(), *report.warnings])
    assert API_KEY not in text
    with pytest.raises(LLMError) as excinfo:
        LLMClient().complete_json("sys", "user")
    assert API_KEY not in str(excinfo.value)


def test_a_missing_base_url_error_names_the_variable_but_not_the_key(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_BASE_URL")

    with pytest.raises(RuntimeError) as excinfo:
        require_llm_attribution_configured()
    assert "FREELLMAPI_BASE_URL" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)

    with pytest.raises(LLMError) as client_error:
        LLMClient().complete_json("sys", "user")
    assert API_KEY not in str(client_error.value)


# --- pipeline wiring ----------------------------------------------------------------


def _pipeline_config(tmp_path, **overrides):
    from book2audio.config import Config

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"x")
    return Config(
        epub_path=str(epub),
        output_path=str(tmp_path / "out.m4b"),
        work_dir=str(tmp_path),
        **overrides,
    )


def _patch_parsed_book(monkeypatch):
    from book2audio import pipeline as pipeline_module
    from book2audio.epub import ParsedChapter
    from book2audio.models import BookMetadata

    parsed = [
        ParsedChapter(index=1, title="One", paragraphs=["Rain fell.", '"Hello there."']),
        ParsedChapter(index=2, title="Two", paragraphs=["Wind blew.", '"Anyone home?"']),
    ]
    monkeypatch.setattr(
        pipeline_module, "parse_epub", lambda *a, **kw: (BookMetadata("T", "A"), parsed)
    )


def test_pipeline_reports_attribution_progress_for_each_window(monkeypatch, tmp_path):
    from book2audio.pipeline import Pipeline

    _patch_parsed_book(monkeypatch)
    _install(monkeypatch, _echo_post("Sunny"))
    ticks = MagicMock()
    pipeline = Pipeline(
        _pipeline_config(tmp_path, attributor="llm"), on_attribution_progress=ticks
    )

    pipeline._load_and_prepare()

    assert ticks.call_count == 2  # one window per chapter


def test_pipeline_dry_run_reports_no_attribution_progress_and_makes_no_request(
    monkeypatch, tmp_path
):
    from book2audio.pipeline import Pipeline

    _patch_parsed_book(monkeypatch)
    post = _install(monkeypatch, MagicMock())
    ticks = MagicMock()
    pipeline = Pipeline(
        _pipeline_config(tmp_path, attributor="llm"), on_attribution_progress=ticks
    )

    pipeline._load_and_prepare(estimate_only=True)

    ticks.assert_not_called()
    post.assert_not_called()


def test_pipeline_only_attributes_the_selected_chapters_with_the_model(monkeypatch, tmp_path):
    from book2audio.pipeline import Pipeline

    _patch_parsed_book(monkeypatch)
    post = _install(monkeypatch, _echo_post("Sunny"))
    pipeline = Pipeline(_pipeline_config(tmp_path, attributor="llm", start_chapter=2))

    pipeline._load_and_prepare()

    assert post.call_count == 1
