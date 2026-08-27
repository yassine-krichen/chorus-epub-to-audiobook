"""Generic OpenAI-compatible image-generation client (freellmapi).

NOTE: this contract is implemented against OpenAI's documented Images API
shape (``POST /v1/images/generations``, ``data[0].b64_json`` or
``data[0].url``). It has NOT been exercised against a live freellmapi
deployment — no real credentials were available at implementation time. This
is deferred verification, not a known defect; the worker-startup fail-fast
check in :func:`require_freellmapi_configured` at least guarantees a job
never gets partway through a render before discovering misconfiguration.
"""

from __future__ import annotations

import base64
import os

import httpx


class ImageGenError(RuntimeError):
    """Raised for any failure generating an image (config, network, shape)."""


def require_freellmapi_configured() -> None:
    """Fail fast if freellmapi env vars are missing.

    Mirrors the worker's existing ``_required_env`` fail-fast style: a job
    that opts into thumbnails without credentials configured should fail
    before any download/render work starts, not partway through.
    """
    missing = [
        name
        for name in ("FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_IMAGE_MODEL")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} environment variable(s) not set for image thumbnails"
        )


def generate_image(
    prompt: str, *, size: str = "512x512", timeout: float = 60.0, max_attempts: int = 2
) -> bytes:
    """Generate one image for ``prompt``, returning raw image bytes.

    Retries once (``max_attempts`` total) on any request/response-shape
    failure. All failure modes normalize to :class:`ImageGenError`.
    """
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            return _request_image(prompt, size=size, timeout=timeout)
        except (
            httpx.HTTPError,
            ImageGenError,
            KeyError,
            IndexError,
            ValueError,
            TypeError,
        ) as exc:
            last_error = exc
    raise ImageGenError(f"Image generation failed: {last_error}") from last_error


def _request_image(prompt: str, *, size: str, timeout: float) -> bytes:
    base_url = os.environ["FREELLMAPI_BASE_URL"].rstrip("/")
    api_key = os.environ["FREELLMAPI_API_KEY"]
    model = os.environ["FREELLMAPI_IMAGE_MODEL"]

    response = httpx.post(
        f"{base_url}/v1/images/generations",
        json={"model": model, "prompt": prompt, "size": size, "n": 1},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    item = payload["data"][0]

    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])
    if "url" in item:
        image_response = httpx.get(item["url"], timeout=timeout)
        image_response.raise_for_status()
        return image_response.content
    raise ImageGenError("Image generation response contained neither b64_json nor url")
