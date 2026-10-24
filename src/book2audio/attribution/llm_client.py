"""Minimal OpenAI-compatible chat client for LLM speaker attribution (freellmapi).

Follows the same convention as ``images/freellmapi.py``: ``FREELLMAPI_BASE_URL``
already includes the ``/v1`` prefix, so this client appends the bare
``/chat/completions`` resource path and must NOT add a second ``/v1``.

The model comes from ``FREELLMAPI_MODEL`` (the same variable the other
freellmapi consumers read, where ``auto`` lets the router pick). Attribution
runs at temperature 0, but a routed model is still not reproducible, so
evaluations pin a concrete model.

The reply is parsed out of the message text, not a ``response_format`` field:
not every routed provider honours it, and models often wrap JSON in code fences.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

DEFAULT_MODEL = "auto"
TEMPERATURE = 0.0
# Room for ~25 labels plus some reasoning tokens on models that spend
# completion budget thinking; still a hard cap on cost per call.
MAX_TOKENS = 2048
# Routed free-tier models can take close to a minute even for small prompts.
TIMEOUT_SECONDS = 120.0
RETRY_BACKOFF_SECONDS = 1.0

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    """Raised for any failure calling the model (config, network, response shape)."""


class _RetryableError(LLMError):
    """A failure worth one more attempt: timeout, connect error, 429 or 5xx."""


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


class LLMClient:
    """Sends one system+user prompt and returns the JSON the model replied with."""

    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float = TEMPERATURE,
        max_tokens: int = MAX_TOKENS,
        timeout: float = TIMEOUT_SECONDS,
        max_attempts: int = 2,
    ) -> None:
        self.model = model or os.environ.get("FREELLMAPI_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts = max_attempts

    def complete_json(self, system: str, user: str) -> Any:
        """Return the parsed JSON reply.

        Retries (``max_attempts`` in total, with a short backoff) only on
        timeouts, connect errors, 429 and 5xx. Auth and request errors
        (400/401/403 and other 4xx) and unusable replies fail at once: another
        attempt would get the same answer. Every failure mode is normalized to
        :class:`LLMError`.
        """
        for attempt in range(1, self.max_attempts + 1):
            try:
                return parse_json_reply(self._request(system, user))
            except _RetryableError as exc:
                if attempt == self.max_attempts:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except LLMError:
                raise
            except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
                raise LLMError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc
        raise LLMError("LLM request failed")  # max_attempts < 1

    def _request(self, system: str, user: str) -> str:
        try:
            base_url = os.environ["FREELLMAPI_BASE_URL"].rstrip("/")
            api_key = os.environ["FREELLMAPI_API_KEY"]
        except KeyError as exc:
            raise LLMError(f"{exc.args[0]} environment variable not set") from exc

        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": self.model,
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
        # TransportError covers timeouts, connect, read and protocol errors.
        except httpx.TransportError as exc:
            raise _RetryableError(f"{type(exc).__name__}: {exc}") from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The body usually says why (e.g. "prompt too large"); it never
            # contains our key, which only travels in the request header.
            message = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code == 429 or response.status_code >= 500:
                raise _RetryableError(message) from exc
            raise LLMError(message) from exc
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM response had no message text")
        return content


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
