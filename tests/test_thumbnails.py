"""Tests for per-chapter thumbnail prompt-building, hashing, and generation.
The freellmapi transport client is mocked -- no real network call."""

from __future__ import annotations

from unittest.mock import MagicMock

from book2audio.images import freellmapi, thumbnails
from book2audio.images.freellmapi import ImageGenError
from book2audio.images.thumbnails import build_prompt, chapter_content_hash, try_generate_thumbnail
from book2audio.models import Chapter, Segment, SegmentKind


def _chapter(index=1, title="Chapter One", text="Once upon a time.") -> Chapter:
    return Chapter(index=index, title=title, segments=[Segment(kind=SegmentKind.NARRATION, text=text)])


def test_build_prompt_includes_the_book_title_chapter_title_and_excerpt():
    chapter = _chapter(title="The Storm", text="Rain lashed the windows.")

    prompt = build_prompt("My Book", chapter)

    assert "My Book" in prompt
    assert "The Storm" in prompt
    assert "Rain lashed the windows." in prompt


def test_build_prompt_excerpt_is_capped_at_400_characters():
    chapter = _chapter(text="x" * 1000)

    prompt = build_prompt("Book", chapter)

    assert "x" * 400 in prompt
    assert "x" * 401 not in prompt


def test_chapter_content_hash_is_stable_for_the_same_title_and_text():
    a = _chapter(title="Same", text="Same text")
    b = _chapter(title="Same", text="Same text")
    assert chapter_content_hash(a) == chapter_content_hash(b)


def test_chapter_content_hash_changes_when_the_text_changes():
    a = _chapter(text="Original text")
    b = _chapter(text="Different text")
    assert chapter_content_hash(a) != chapter_content_hash(b)


def test_chapter_content_hash_changes_when_the_title_changes():
    a = _chapter(title="Old Title", text="Same body")
    b = _chapter(title="New Title", text="Same body")
    assert chapter_content_hash(a) != chapter_content_hash(b)


def test_chapter_content_hash_is_unaffected_by_chapter_index():
    """Only the *image-relevant* content should invalidate the cache -- a
    chapter renumbered by an upstream edit shouldn't force regeneration."""
    a = _chapter(index=1, title="T", text="Body")
    b = _chapter(index=99, title="T", text="Body")
    assert chapter_content_hash(a) == chapter_content_hash(b)


def test_try_generate_thumbnail_returns_the_generated_bytes_on_success(monkeypatch):
    monkeypatch.setattr(thumbnails, "generate_image", MagicMock(return_value=b"png-bytes"))

    result = try_generate_thumbnail("Book", _chapter())

    assert result == b"png-bytes"


def test_try_generate_thumbnail_returns_none_and_does_not_raise_on_image_gen_error(monkeypatch):
    monkeypatch.setattr(
        thumbnails, "generate_image", MagicMock(side_effect=ImageGenError("boom"))
    )

    result = try_generate_thumbnail("Book", _chapter())

    assert result is None


def test_try_generate_thumbnail_returns_none_for_a_malformed_upstream_response_shape(monkeypatch):
    """generate_image normalizes even a TypeError-raising malformed response
    shape (e.g. a None element in `data`) to ImageGenError -- confirms
    try_generate_thumbnail's "never raises" contract holds end-to-end, not
    just when generate_image is mocked to raise ImageGenError directly."""
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://freellmapi.example.com")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "test-key")
    monkeypatch.setenv("FREELLMAPI_IMAGE_MODEL", "test-model")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": [None]}
    monkeypatch.setattr(freellmapi.httpx, "post", MagicMock(return_value=response))

    result = try_generate_thumbnail("Book", _chapter())

    assert result is None
