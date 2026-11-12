"""The model chain in ``LLMClient``: rotation, cooling, Retry-After and per-task models.

``httpx.post`` is mocked at ``book2audio.attribution.llm_client``, the same way
``test_attribution_llm.py`` does it. The mock reads the requested model out of the
JSON body, so tests can script one behaviour per model.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from book2audio.attribution import LLMAttributor, build_attributor
from book2audio.attribution import llm_client
from book2audio.attribution.llm import AttributionCache
from book2audio.attribution.llm_client import (
    DEFAULT_CHAINS,
    LLMClient,
    LLMError,
    parse_model_chain,
    resolve_model_chain,
)
from book2audio.config import Config
from book2audio.images.enhanced import build_enhanced_thumbnails
from book2audio.models import Chapter, Segment, SegmentKind

API_KEY = "sk-test-SECRET-9f3a"
OK = '{"labels": []}'


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com/v1")
    monkeypatch.setenv("FREELLMAPI_API_KEY", API_KEY)
    for name in ("FREELLMAPI_MODEL", "FREELLMAPI_MODEL_ATTRIBUTION", "FREELLMAPI_MODEL_LIGHT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _sleeps(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    return sleeps


def _ok(content=OK):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def _error(status, text="nope", retry_after=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.headers = {} if retry_after is None else {"retry-after": retry_after}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=response
    )
    return response


def _gateway(monkeypatch, behaviour):
    """Install a fake ``httpx.post``. ``behaviour`` maps a model to a response, an
    exception, or a list of them replayed in order (the last one repeats)."""
    asked: list[str] = []
    queues = {m: list(v) if isinstance(v, list) else [v] for m, v in behaviour.items()}

    def post(url, **kwargs):
        model = kwargs["json"]["model"]
        asked.append(model)
        queue = queues[model]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_client.httpx, "post", post)
    return asked


# --- resolving the chain ----------------------------------------------------------


def test_a_single_model_in_the_environment_is_a_chain_of_one(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL", "gpt-oss-120b")

    assert LLMClient().models == ("gpt-oss-120b",)


def test_a_comma_separated_list_keeps_its_order_and_drops_blanks_and_repeats(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL", " a , b,, a ,c ")

    assert LLMClient().models == ("a", "b", "c")


def test_parse_model_chain_of_nothing_is_empty():
    assert parse_model_chain(" , ") == ()


def test_unset_models_fall_back_to_the_built_in_chain_not_auto():
    for task in ("attribution", "light"):
        chain = resolve_model_chain(task)
        assert chain == DEFAULT_CHAINS[task]
        assert chain and "auto" not in chain


def test_the_per_task_variable_beats_the_shared_one(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL", "shared")
    monkeypatch.setenv("FREELLMAPI_MODEL_ATTRIBUTION", "strong-a,strong-b")
    monkeypatch.setenv("FREELLMAPI_MODEL_LIGHT", "cheap")

    assert LLMClient(task="attribution").models == ("strong-a", "strong-b")
    assert LLMClient(task="light").models == ("cheap",)


def test_the_shared_variable_covers_a_task_without_its_own(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL", "shared")
    monkeypatch.setenv("FREELLMAPI_MODEL_LIGHT", "cheap")

    assert LLMClient(task="attribution").models == ("shared",)
    assert LLMClient(task="light").models == ("cheap",)


def test_an_explicit_model_argument_beats_every_variable(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL_ATTRIBUTION", "from-env")

    assert LLMClient(model="pinned-a, pinned-b").models == ("pinned-a", "pinned-b")


def test_attribution_and_scene_briefs_ask_for_their_own_task(monkeypatch, tmp_path):
    monkeypatch.setenv("FREELLMAPI_MODEL_ATTRIBUTION", "strong")
    monkeypatch.setenv("FREELLMAPI_MODEL_LIGHT", "cheap")

    attributor = build_attributor(
        Config(epub_path="x.epub", output_path="x.m4b", work_dir=str(tmp_path), attributor="hybrid")
    )
    enhanced = build_enhanced_thumbnails(str(tmp_path), "Book")

    assert attributor._client.models == ("strong",)
    assert attributor._judge_client.models == ("cheap",)  # the alias and gender judges
    assert enhanced is not None and enhanced._client.models == ("cheap",)


# --- rotation ----------------------------------------------------------------------


def test_a_rate_limited_model_hands_the_request_to_the_next_one(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _error(429), "b": _ok('{"labels": [1]}')})
    client = LLMClient(model="a,b")

    assert client.complete_json("sys", "user") == {"labels": [1]}
    assert asked == ["a", "b"]


def test_a_cooled_model_is_not_asked_again_for_the_rest_of_the_run(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _error(429), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    client.complete_json("sys", "two")
    client.complete_json("sys", "three")

    assert asked == ["a", "b", "b", "b"]
    assert client.cooled_models == ("a",)


def test_a_new_client_starts_with_nothing_cooled(monkeypatch):
    _gateway(monkeypatch, {"a": _error(429), "b": _ok()})
    LLMClient(model="a,b").complete_json("sys", "user")

    assert LLMClient(model="a,b").cooled_models == ()


def test_the_client_counts_requests_and_rate_limits(monkeypatch):
    _gateway(monkeypatch, {"a": _error(429), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    client.complete_json("sys", "two")

    assert (client.requests, client.rate_limited) == (3, 1)


def test_server_errors_are_retried_once_on_the_same_model_before_rotating(
    monkeypatch, _sleeps
):
    asked = _gateway(monkeypatch, {"a": _error(503), "b": _ok()})

    LLMClient(model="a,b").complete_json("sys", "user")

    assert asked == ["a", "a", "b"]
    assert len(_sleeps) == 1


def test_a_timeout_cools_the_model_after_its_retry(monkeypatch):
    asked = _gateway(monkeypatch, {"a": httpx.ReadTimeout("slow"), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "user")

    assert asked == ["a", "a", "b"]
    assert client.cooled_models == ("a",)


def test_a_model_the_gateway_does_not_know_is_skipped(monkeypatch):
    asked = _gateway(monkeypatch, {"typo": _error(404, "not in the catalog"), "b": _ok()})
    client = LLMClient(model="typo,b")

    client.complete_json("sys", "user")

    assert asked == ["typo", "b"]  # a 404 is not retried


def test_a_rejected_key_fails_the_call_without_rotating(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _error(401), "b": _ok()})

    with pytest.raises(LLMError, match="401"):
        LLMClient(model="a,b").complete_json("sys", "user")

    assert asked == ["a"]


def test_a_403_rotates_because_some_gateways_use_it_for_a_model_not_on_the_tier(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _error(403, "model not on your tier"), "b": _ok()})

    LLMClient(model="a,b").complete_json("sys", "user")

    assert asked == ["a", "b"]


def test_two_models_in_a_row_answering_403_is_the_key_so_rotation_stops(monkeypatch):
    asked = _gateway(
        monkeypatch, {"a": _error(403), "b": _error(403), "c": _ok()}
    )

    with pytest.raises(LLMError, match="403"):
        LLMClient(model="a,b,c").complete_json("sys", "user")

    assert asked == ["a", "b"]


def test_an_unknown_model_is_cooled_for_the_rest_of_the_run(monkeypatch):
    _gateway(monkeypatch, {"typo": _error(404), "b": _ok()})
    client = LLMClient(model="typo,b")

    client.complete_json("sys", "user")

    assert client.cooled_models == ("typo",)


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_request_the_model_rejected_takes_a_strike_instead_of_cooling_it(monkeypatch, status):
    # A content filter or an oversized prompt is about one window, not the model.
    asked = _gateway(monkeypatch, {"a": [_error(status), _ok()], "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    assert asked == ["a", "b"]
    assert client.cooled_models == ()  # first strike only

    client.complete_json("sys", "two")  # a answers this window
    assert asked == ["a", "b", "a"]
    assert client.cooled_models == ()


@pytest.mark.parametrize("status", [400, 413, 422])
def test_two_rejected_requests_in_a_row_cool_the_model(monkeypatch, status):
    _gateway(monkeypatch, {"a": _error(status), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    client.complete_json("sys", "two")

    assert client.cooled_models == ("a",)


def test_an_outage_cools_the_model_for_a_while_not_for_the_whole_run(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    _gateway(monkeypatch, {"a": [_error(503), _error(503), _ok()], "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    assert client.cooled_models == ("a",)

    now[0] += llm_client.OUTAGE_COOLDOWN_SECONDS + 1
    assert client.cooled_models == ()


def test_a_timeout_cooldown_is_finite_too(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    _gateway(monkeypatch, {"a": httpx.ReadTimeout("slow"), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    assert client.cooled_models == ("a",)

    now[0] += llm_client.OUTAGE_COOLDOWN_SECONDS + 1
    assert client.cooled_models == ()


def test_a_429_without_retry_after_still_cools_for_the_rest_of_the_run(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    _gateway(monkeypatch, {"a": _error(429), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")

    now[0] += 10**9
    assert client.cooled_models == ("a",)


def test_a_non_transport_http_error_rotates_without_a_retry(monkeypatch, _sleeps):
    asked = _gateway(monkeypatch, {"a": httpx.TooManyRedirects("loop"), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "user")

    assert asked == ["a", "b"]
    assert _sleeps == []
    assert client.cooled_models == ()  # a strike, not a rest


def test_two_non_transport_http_errors_in_a_row_cool_the_model_for_good(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    _gateway(monkeypatch, {"a": httpx.DecodingError("bad gzip"), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    client.complete_json("sys", "two")

    now[0] += 10**9
    assert client.cooled_models == ("a",)


def test_a_non_transport_http_error_on_every_model_is_an_llm_error(monkeypatch):
    _gateway(monkeypatch, {"a": httpx.DecodingError("bad gzip")})

    with pytest.raises(LLMError, match="DecodingError"):
        LLMClient(model="a").complete_json("sys", "user")


# --- Retry-After -------------------------------------------------------------------


def test_a_short_retry_after_is_waited_out_on_the_same_model(monkeypatch, _sleeps):
    asked = _gateway(monkeypatch, {"a": [_error(429, retry_after="2"), _ok()], "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "user")

    assert asked == ["a", "a"]
    assert _sleeps == [2.0]
    assert client.cooled_models == ()


def test_a_long_retry_after_moves_on_and_cools_the_model_until_it_passes(
    monkeypatch, _sleeps
):
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    asked = _gateway(monkeypatch, {"a": [_error(429, retry_after="60"), _ok()], "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    assert (asked, _sleeps, client.cooled_models) == (["a", "b"], [], ("a",))

    now[0] += 30
    client.complete_json("sys", "two")
    assert asked == ["a", "b", "b"]

    now[0] += 31  # past the Retry-After: a is first choice again
    client.complete_json("sys", "three")
    assert asked == ["a", "b", "b", "a"]


@pytest.mark.parametrize("header", ["soon", "Wed, 21 Oct 2026 07:28:00 GMT", "-5", "nan"])
def test_an_unreadable_retry_after_is_treated_as_absent(monkeypatch, _sleeps, header):
    asked = _gateway(monkeypatch, {"a": _error(429, retry_after=header), "b": _ok()})

    LLMClient(model="a,b").complete_json("sys", "user")

    assert asked == ["a", "b"]
    assert _sleeps == []


# --- unusable replies --------------------------------------------------------------


def test_an_unparseable_reply_goes_to_the_next_model_without_cooling_the_first(monkeypatch):
    asked = _gateway(monkeypatch, {"a": [_ok("not json"), _ok()], "b": _ok('{"labels": [2]}')})
    client = LLMClient(model="a,b")

    assert client.complete_json("sys", "one") == {"labels": [2]}
    assert client.cooled_models == ()
    client.complete_json("sys", "two")  # a gets another chance and answers
    assert asked == ["a", "b", "a"]


def test_a_model_with_two_unusable_replies_in_a_row_is_cooled(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _ok("not json"), "b": _ok()})
    client = LLMClient(model="a,b")

    client.complete_json("sys", "one")
    client.complete_json("sys", "two")
    client.complete_json("sys", "three")

    assert asked == ["a", "b", "a", "b", "b"]
    assert client.cooled_models == ("a",)


def test_a_good_reply_clears_a_models_strikes(monkeypatch):
    _gateway(monkeypatch, {"a": [_ok("not json"), _ok(), _ok("not json"), _ok()], "b": _ok()})
    client = LLMClient(model="a,b")

    for prompt in ("one", "two", "three", "four"):
        client.complete_json("sys", prompt)

    assert client.cooled_models == ()


def test_a_reply_of_the_wrong_shape_rotates_when_a_validator_is_given(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _ok('{"oops": 1}'), "b": _ok('{"labels": [3]}')})

    def need_labels(reply):
        if "labels" not in reply:
            raise LLMError("no labels")

    reply = LLMClient(model="a,b").complete_json("sys", "user", validate=need_labels)

    assert reply == {"labels": [3]}
    assert asked == ["a", "b"]


def test_an_empty_message_counts_as_an_unusable_reply(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _ok(""), "b": _ok()})

    LLMClient(model="a,b").complete_json("sys", "user")

    assert asked == ["a", "b"]


# --- exhaustion --------------------------------------------------------------------


def test_the_call_fails_with_every_models_reason_when_all_of_them_fail(monkeypatch):
    _gateway(monkeypatch, {"a": _error(429, "daily cap"), "b": _error(404, "gone")})

    with pytest.raises(LLMError) as excinfo:
        LLMClient(model="a,b").complete_json("sys", "user")

    message = str(excinfo.value)
    assert "every model" in message
    assert "a: HTTP 429" in message and "b: HTTP 404" in message
    assert API_KEY not in message


def test_the_last_model_standing_is_never_cooled(monkeypatch):
    asked = _gateway(monkeypatch, {"a": _error(429), "b": _error(429)})
    client = LLMClient(model="a,b")

    for prompt in ("one", "two"):
        with pytest.raises(LLMError):
            client.complete_json("sys", prompt)

    assert asked == ["a", "b", "b"]
    assert client.cooled_models == ("a",)


def test_a_single_model_chain_keeps_asking_after_a_failure(monkeypatch):
    asked = _gateway(monkeypatch, {"a": [_error(429), _ok()]})
    client = LLMClient(model="a")

    with pytest.raises(LLMError):
        client.complete_json("sys", "one")

    assert client.complete_json("sys", "two") == {"labels": []}
    assert asked == ["a", "a"]


# --- circuit breaker and cache -----------------------------------------------------


def _dlg(text):
    return Segment(kind=SegmentKind.DIALOGUE, text=text)


def _scenes(count):
    """One untagged line per scene: every window is its own LLM call in hybrid mode."""
    segments: list[Segment] = []
    for n in range(count):
        if n:
            segments.append(Segment(kind=SegmentKind.SCENE_BREAK, text=""))
        segments += [Segment(kind=SegmentKind.NARRATION, text="Sunny waited."), _dlg(f"Line {n}.")]
    return Chapter(index=1, title="Ch", segments=segments)


def _labelling_gateway(monkeypatch, failing):
    """``failing`` models answer 429; every other model labels each requested id 'Sunny'."""
    asked: list[str] = []

    def post(url, **kwargs):
        model = kwargs["json"]["model"]
        asked.append(model)
        if model in failing:
            return _error(429)
        prompt = kwargs["json"]["messages"][1]["content"]
        ids = [int(i) for i in prompt.rsplit("Label dialogue ids: ", 1)[1].split(",")]
        return _ok(json.dumps({"labels": [{"id": i, "speaker": "Sunny"} for i in ids]}))

    monkeypatch.setattr(llm_client.httpx, "post", post)
    return asked


def test_one_models_rate_limit_does_not_trip_the_circuit_breaker(monkeypatch):
    asked = _labelling_gateway(monkeypatch, failing={"a"})
    attributor = LLMAttributor("hybrid", LLMClient(model="a,b"), AttributionCache(None))

    attributor.attribute([_scenes(6)])

    report = attributor.report
    assert report.breaker_tripped is False
    assert report.windows_failed == 0 and report.windows_unprocessed == 0
    assert report.calls == 6  # the budget counts windows, not HTTP requests
    assert asked == ["a"] + ["b"] * 6


def test_the_breaker_trips_when_every_model_is_exhausted(monkeypatch):
    _labelling_gateway(monkeypatch, failing={"a", "b"})
    attributor = LLMAttributor("hybrid", LLMClient(model="a,b"), AttributionCache(None))

    attributor.attribute([_scenes(6)])

    report = attributor.report
    assert report.breaker_tripped is True
    assert report.windows_failed == 3
    assert report.windows_unprocessed == 3


def test_a_wrong_shape_reply_from_the_first_model_is_answered_by_the_second(monkeypatch):
    asked: list[str] = []

    def post(url, **kwargs):
        model = kwargs["json"]["model"]
        asked.append(model)
        if model == "a":
            return _ok('{"labels": "nope"}')
        prompt = kwargs["json"]["messages"][1]["content"]
        ids = [int(i) for i in prompt.rsplit("Label dialogue ids: ", 1)[1].split(",")]
        return _ok(json.dumps({"labels": [{"id": i, "speaker": "Sunny"} for i in ids]}))

    monkeypatch.setattr(llm_client.httpx, "post", post)
    attributor = LLMAttributor("hybrid", LLMClient(model="a,b"), AttributionCache(None))

    attributor.attribute([_scenes(1)])

    assert attributor.report.windows_failed == 0
    assert asked == ["a", "b"]


def test_a_single_model_in_the_environment_still_drives_attribution(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_MODEL", "only-model")
    asked = _labelling_gateway(monkeypatch, failing=set())
    attributor = LLMAttributor("hybrid", LLMClient(), AttributionCache(None))

    attributor.attribute([_scenes(2)])

    assert asked == ["only-model", "only-model"]


def test_the_403_count_starts_over_after_a_different_failure(monkeypatch):
    asked = _gateway(
        monkeypatch,
        {"a": _error(403), "b": _error(400), "c": _error(403), "d": _ok()},
    )

    LLMClient(model="a,b,c,d").complete_json("sys", "user")

    assert asked == ["a", "b", "c", "d"]
