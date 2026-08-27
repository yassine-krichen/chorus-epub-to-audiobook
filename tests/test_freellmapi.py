"""Tests for the freellmapi image-generation client. httpx is mocked -- no
real network call or credentials are used or required.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import httpx
import pytest

from book2audio.images import freellmapi
from book2audio.images.freellmapi import ImageGenError, generate_image, require_freellmapi_configured


@pytest.fixture(autouse=True)
def _freellmapi_env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "test-key")
    monkeypatch.setenv("FREELLMAPI_IMAGE_MODEL", "test-model")


def _response(*, json_body=None, content=b""):
    response = MagicMock()
    response.raise_for_status.return_value = None
    if json_body is not None:
        response.json.return_value = json_body
    response.content = content
    return response


def test_require_freellmapi_configured_passes_when_every_env_var_is_set():
    require_freellmapi_configured()  # must not raise


@pytest.mark.parametrize(
    "missing", ["FREELLMAPI_BASE_URL", "FREELLMAPI_API_KEY", "FREELLMAPI_IMAGE_MODEL"]
)
def test_require_freellmapi_configured_raises_when_an_env_var_is_missing(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(RuntimeError, match=missing):
        require_freellmapi_configured()


def test_generate_image_decodes_a_b64_json_response(monkeypatch):
    image_bytes = b"fake-png-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    post = MagicMock(return_value=_response(json_body={"data": [{"b64_json": encoded}]}))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    result = generate_image("a book cover")

    assert result == image_bytes
    post.assert_called_once()


def test_generate_image_fetches_bytes_from_a_url_response(monkeypatch):
    image_bytes = b"fetched-bytes"
    post = MagicMock(
        return_value=_response(json_body={"data": [{"url": "https://img.example.com/1.png"}]})
    )
    get = MagicMock(return_value=_response(content=image_bytes))
    monkeypatch.setattr(freellmapi.httpx, "post", post)
    monkeypatch.setattr(freellmapi.httpx, "get", get)

    result = generate_image("a book cover")

    assert result == image_bytes
    get.assert_called_once_with("https://img.example.com/1.png", timeout=60.0)


def test_generate_image_retries_once_after_a_transient_failure_then_succeeds(monkeypatch):
    image_bytes = b"ok-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    success = _response(json_body={"data": [{"b64_json": encoded}]})
    post = MagicMock(side_effect=[httpx.ConnectError("boom"), success])
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    result = generate_image("a book cover")

    assert result == image_bytes
    assert post.call_count == 2


def test_generate_image_raises_image_gen_error_after_exhausting_retries(monkeypatch):
    post = MagicMock(side_effect=httpx.ConnectError("boom"))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    with pytest.raises(ImageGenError):
        generate_image("a book cover")

    assert post.call_count == 2


def test_generate_image_normalizes_a_raw_httpx_error_to_image_gen_error(monkeypatch):
    """Callers should only ever have to catch ImageGenError, not a grab bag
    of transport exceptions."""
    post = MagicMock(side_effect=httpx.ConnectError("boom"))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    try:
        generate_image("a book cover")
        pytest.fail("expected ImageGenError")
    except ImageGenError:
        pass
    except httpx.HTTPError:
        pytest.fail("raw httpx error leaked out of generate_image")


def test_generate_image_raises_image_gen_error_when_response_has_neither_shape(monkeypatch):
    post = MagicMock(return_value=_response(json_body={"data": [{}]}))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    with pytest.raises(ImageGenError):
        generate_image("a book cover", max_attempts=1)


def test_generate_image_raises_image_gen_error_when_the_data_list_is_empty(monkeypatch):
    post = MagicMock(return_value=_response(json_body={"data": []}))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    with pytest.raises(ImageGenError):
        generate_image("a book cover", max_attempts=1)


def test_generate_image_raises_image_gen_error_when_a_data_element_is_none(monkeypatch):
    """A malformed upstream response shape (e.g. data as a list of None)
    would raise TypeError from `"b64_json" in item` -- generate_image must
    normalize that to ImageGenError too, not let it leak past
    try_generate_thumbnail's "never raises" contract."""
    post = MagicMock(return_value=_response(json_body={"data": [None]}))
    monkeypatch.setattr(freellmapi.httpx, "post", post)

    with pytest.raises(ImageGenError):
        generate_image("a book cover", max_attempts=1)
