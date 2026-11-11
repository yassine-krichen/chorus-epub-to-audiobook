"""Minimal OpenAI-compatible chat client for LLM speaker attribution (freellmapi).

Follows the same convention as ``images/freellmapi.py``: ``FREELLMAPI_BASE_URL``
already includes the ``/v1`` prefix, so this client appends the bare
``/chat/completions`` resource path and must NOT add a second ``/v1``.

Models come as an ordered *chain*, so one model's free-tier cap never ends a run
(gemini-3.1-flash-lite-preview allows 20 requests a day, for example). The chain is
the first of these that is set, else the built-in default for the task:

1. ``FREELLMAPI_MODEL_ATTRIBUTION`` / ``FREELLMAPI_MODEL_LIGHT`` (per task)
2. ``FREELLMAPI_MODEL`` (every task; one name or a comma-separated list)

A model that answers 429, 5xx, times out or keeps returning unusable replies is
"cooled" for the rest of the run (or for its ``Retry-After``, when it sent one) and
the next model gets the request; the last model standing is never cooled.
:class:`LLMError` is raised only when every model failed the request, which is what
lets the attribution circuit breaker mean "the whole chain is down" and not "one model
hit its cap". Attribution runs at temperature 0, but a
routed model is still not reproducible, so evaluations pin a concrete model.

The reply is parsed out of the message text, not a ``response_format`` field:
not every routed provider honours it, and models often wrap JSON in code fences.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

Task = Literal["attribution", "light"]

# attribution: who speaks each line needs a capable model. light: short or
# forgiving jobs (scene brief, gender judge) run on cheap ones. Free-tier daily
# caps are why each chain has several entries; see docs/features/llm-model-chain.md
# for the measurements behind the order.
DEFAULT_CHAINS: dict[str, tuple[str, ...]] = {
    "attribution": ("gemini-2.5-flash-lite", "gpt-oss-120b", "llama-4-scout"),
    "light": ("gemini-2.5-flash-lite", "gpt-oss-20b", "gpt-oss-120b"),
}
TASK_ENV_VARS: dict[str, str] = {
    "attribution": "FREELLMAPI_MODEL_ATTRIBUTION",
    "light": "FREELLMAPI_MODEL_LIGHT",
}
SHARED_ENV_VAR = "FREELLMAPI_MODEL"

TEMPERATURE = 0.0
# Room for ~25 labels plus some reasoning tokens on models that spend
# completion budget thinking; still a hard cap on cost per call.
MAX_TOKENS = 2048
# Routed free-tier models can take close to a minute even for small prompts.
TIMEOUT_SECONDS = 120.0
RETRY_BACKOFF_SECONDS = 1.0
# A Retry-After up to this long is waited out on the same model; a longer one
# means the model is capped for a while and the next model takes over.
MAX_RETRY_AFTER_SECONDS = 10.0
# One unusable reply is often just that window; a model that fails twice in a
# row is not worth another request.
MAX_BAD_REPLIES = 2
# A rejected key belongs to the key, not the model: rotating cannot help. 403 is
# deliberately not here: some gateways answer it for "model not on your tier".
_KEY_WIDE_STATUSES = frozenset({401})
# How long a model rests after a timeout, a 5xx without Retry-After or a network
# error: the outage is probably brief, so it must not be skipped for the whole run.
OUTAGE_COOLDOWN_SECONDS = 120.0
# Status for an unknown model name: no later request can fix that.
_UNKNOWN_MODEL_STATUS = 404

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    """Raised for any failure calling the model (config, network, response shape)."""


class _ModelFailure(Exception):
    """One model could not answer: the caller cools it and tries the next one."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        bad_reply: bool = False,
        outage: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable  # worth another attempt on the same model
        self.retry_after = retry_after  # seconds the provider asked us to wait
        self.bad_reply = bad_reply  # the model answered, but not usably
        self.outage = outage  # probably brief (timeout, 5xx): cool for a finite time


def require_llm_attribution_configured() -> None:
    """Fail fast if freellmapi credentials are missing.

    Names only, never values: this message is printed to the console and logs.
    """
    missing = [
        name for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY") if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} environment variable(s) not set for LLM attribution"
        )


def parse_model_chain(raw: str) -> tuple[str, ...]:
    """Split a comma-separated model list, dropping blanks and repeats (order kept)."""
    names = (name.strip() for name in raw.split(","))
    return tuple(dict.fromkeys(name for name in names if name))


def resolve_model_chain(task: Task) -> tuple[str, ...]:
    """The ordered models for ``task``: task variable, then shared one, then default."""
    for variable in (TASK_ENV_VARS[task], SHARED_ENV_VAR):
        chain = parse_model_chain(os.environ.get(variable, ""))
        if chain:
            return chain
    return DEFAULT_CHAINS[task]


class LLMClient:
    """Sends one system+user prompt and returns the JSON the model replied with.

    One instance is one run: it remembers which models it has cooled. ``model``
    (a name or a comma-separated list) overrides the environment, e.g. for evals.
    Not thread-safe: the cooling state and counters are plain attributes, so
    give each thread its own instance.
    """

    def __init__(
        self,
        *,
        task: Task = "attribution",
        model: str | None = None,
        temperature: float = TEMPERATURE,
        max_tokens: int = MAX_TOKENS,
        timeout: float = TIMEOUT_SECONDS,
        max_attempts: int = 2,
    ) -> None:
        self.models = parse_model_chain(model or "") or resolve_model_chain(task)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts = max_attempts
        # Monotonic deadline per cooled model; infinity means "rest of the run".
        self._cooled_until: dict[str, float] = {}
        self._bad_replies: dict[str, int] = {}
        self.requests = 0  # HTTP requests sent, across all models
        self.rate_limited = 0  # of those, answered 429

    @property
    def cooled_models(self) -> tuple[str, ...]:
        """Models currently skipped, in chain order."""
        now = time.monotonic()
        return tuple(m for m in self.models if self._cooled_until.get(m, 0.0) > now)

    def complete_json(
        self, system: str, user: str, validate: Callable[[Any], object] | None = None
    ) -> Any:
        """Return the parsed JSON reply from the first model that gives a usable one.

        ``validate`` may raise :class:`LLMError` for a reply that parses but has
        the wrong shape; that counts as an unusable reply from that model.

        Per model: a timeout, connect error or 5xx is retried once (``max_attempts``
        in total, with a short backoff), and so is a 429 whose ``Retry-After`` is
        short. Any other 429, and any other failure, cools the model at once.
        A 401 fails the whole call: another model would get the same answer.
        :class:`LLMError` means every live model failed this request.
        """
        failures: list[str] = []
        for model in self._live_models():
            try:
                reply = self._ask(model, system, user, validate)
            except _ModelFailure as failure:
                self._cool(model, failure)
                failures.append(f"{model}: {failure}")
                continue
            self._bad_replies.pop(model, None)
            return reply
        raise LLMError(f"LLM request failed on every model ({'; '.join(failures)})")

    def _live_models(self) -> list[str]:
        cooled = self.cooled_models
        return [m for m in self.models if m not in cooled]

    def _ask(
        self, model: str, system: str, user: str, validate: Callable[[Any], object] | None
    ) -> Any:
        for attempt in range(1, self.max_attempts + 1):
            try:
                text = self._request(model, system, user)
            except _ModelFailure as failure:
                if not failure.retryable or attempt == self.max_attempts:
                    raise
                wait = failure.retry_after
                time.sleep(RETRY_BACKOFF_SECONDS * attempt if wait is None else wait)
                continue
            try:
                reply = parse_json_reply(text)
                if validate is not None:
                    validate(reply)
            except LLMError as exc:
                raise _ModelFailure(str(exc), bad_reply=True) from exc
            return reply
        raise _ModelFailure("no attempts allowed")  # max_attempts < 1

    def _cool(self, model: str, failure: _ModelFailure) -> None:
        if self._live_models() == [model]:
            # The last model standing is still the best chance this request has,
            # and a single-model chain then behaves as it always did: every
            # window tries again and the circuit breaker decides when to stop.
            return
        if failure.bad_reply:
            strikes = self._bad_replies.get(model, 0) + 1
            self._bad_replies[model] = strikes
            if strikes < MAX_BAD_REPLIES:
                return
        # A Retry-After says when the model is usable again. Without one, a 429
        # is assumed to be a daily cap (free tiers send no header) and lasts for
        # the run, while an outage gets a finite rest.
        wait = failure.retry_after
        if wait is None:
            wait = OUTAGE_COOLDOWN_SECONDS if failure.outage else math.inf
        self._cooled_until[model] = time.monotonic() + wait
        logger.warning(
            "LLM model %s cooled (%s); the next model in the chain takes over", model, failure
        )

    def _request(self, model: str, system: str, user: str) -> str:
        try:
            base_url = os.environ["FREELLMAPI_BASE_URL"].rstrip("/")
            api_key = os.environ["FREELLMAPI_API_KEY"]
        except KeyError as exc:
            raise LLMError(f"{exc.args[0]} environment variable not set") from exc

        self.requests += 1
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self.timeout,
            )
        # HTTPError is the base of everything httpx raises while sending: transport
        # errors (timeouts, connect, protocol) are worth a retry; the rest
        # (too many redirects, bad URL, undecodable body) are not.
        except httpx.HTTPError as exc:
            raise _ModelFailure(
                f"{type(exc).__name__}: {exc}",
                retryable=isinstance(exc, httpx.TransportError),
                outage=True,
            ) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The body usually says why (e.g. "prompt too large"); it never
            # contains our key, which only travels in the request header.
            status = response.status_code
            message = f"HTTP {status}: {response.text[:200]}"
            if status in _KEY_WIDE_STATUSES:
                raise LLMError(message) from exc
            retry_after = _retry_after_seconds(response)
            short = retry_after is not None and retry_after <= MAX_RETRY_AFTER_SECONDS
            if status == 429:
                # No hint usually means a daily cap: waiting would only burn a request.
                self.rate_limited += 1
                raise _ModelFailure(message, retryable=short, retry_after=retry_after) from exc
            if status >= 500:
                raise _ModelFailure(
                    message,
                    retryable=retry_after is None or short,
                    retry_after=retry_after,
                    outage=True,
                ) from exc
            if status == _UNKNOWN_MODEL_STATUS:
                # The name is wrong, and that stays true for the whole run.
                raise _ModelFailure(message) from exc
            # Other 4xx (content filter, prompt too large for this one, validation)
            # are about this request, not the model: retrying it cannot help, but
            # the model may well answer the next window, so it only takes a strike.
            raise _ModelFailure(message, bad_reply=True) from exc
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise _ModelFailure(
                f"unexpected response shape ({type(exc).__name__})", bad_reply=True
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise _ModelFailure("response had no message text", bad_reply=True)
        return content


def _retry_after_seconds(response: Any) -> float | None:
    """The ``Retry-After`` header as seconds, or None when absent or not a number.

    The HTTP-date form is ignored: gateways in front of free-tier providers send
    seconds, and an unreadable value just means "no hint".
    """
    raw = response.headers.get("retry-after")
    if not isinstance(raw, str):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def parse_json_reply(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    cleaned = text.strip()
    fenced = _FENCE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("LLM reply was not valid JSON")
