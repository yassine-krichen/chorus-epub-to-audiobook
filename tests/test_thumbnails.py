"""Tests for per-chapter thumbnail prompt-building, hashing, and generation.
The freellmapi transport client is mocked -- no real network call."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from book2audio.images import freellmapi, thumbnails
from book2audio.images.freellmapi import ImageGenError
from book2audio.images.thumbnails import (
    TEMPLATE_VERSION,
    build_prompt,
    chapter_content_hash,
    clean_book_title,
    image_file_type,
    try_generate_thumbnail,
)
from book2audio.models import Chapter, Segment, SegmentKind


def _chapter(index=1, title="Chapter One", text="Once upon a time.", number=-1) -> Chapter:
    return Chapter(
        index=index,
        title=title,
        number=number,
        segments=[Segment(kind=SegmentKind.NARRATION, text=text)],
    )


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


def test_build_prompt_excerpt_ends_on_a_whole_sentence_when_truncated():
    sentence = "The young man waited on the bench. "
    chapter = _chapter(text=sentence * 30)

    prompt = build_prompt("Book", chapter)

    assert "waited on the bench. Style:" in prompt


def test_build_prompt_excerpt_ends_on_a_whole_word_when_there_is_no_sentence_break():
    chapter = _chapter(text="walking " * 100)

    prompt = build_prompt("Book", chapter)

    assert "walking... Style:" in prompt
    assert "walkin... " not in prompt


def test_chapter_content_hash_is_stable_for_the_same_title_and_text():
    a = _chapter(title="Same", text="Same text")
    b = _chapter(title="Same", text="Same text")
    assert chapter_content_hash(a, "Book") == chapter_content_hash(b, "Book")


def test_chapter_content_hash_changes_when_the_text_changes():
    a = _chapter(text="Original text")
    b = _chapter(text="Different text")
    assert chapter_content_hash(a, "Book") != chapter_content_hash(b, "Book")


def test_chapter_content_hash_changes_when_the_title_changes():
    a = _chapter(title="Old Title", text="Same body")
    b = _chapter(title="New Title", text="Same body")
    assert chapter_content_hash(a, "Book") != chapter_content_hash(b, "Book")


def test_chapter_content_hash_is_unaffected_by_chapter_index():
    """Only the *image-relevant* content should invalidate the cache -- a
    chapter renumbered by an upstream edit shouldn't force regeneration."""
    a = _chapter(index=1, number=5, title="T", text="Body")
    b = _chapter(index=99, number=5, title="T", text="Body")
    assert chapter_content_hash(a, "Book") == chapter_content_hash(b, "Book")


def test_chapter_content_hash_changes_when_the_book_chapter_number_changes():
    a = _chapter(number=1, title="T", text="Body")
    b = _chapter(number=2, title="T", text="Body")
    assert chapter_content_hash(a, "Book") != chapter_content_hash(b, "Book")


def test_chapter_content_hash_changes_when_the_template_version_changes(monkeypatch):
    chapter = _chapter()
    before = chapter_content_hash(chapter, "Book")

    monkeypatch.setattr(thumbnails, "TEMPLATE_VERSION", TEMPLATE_VERSION + "-next")

    assert chapter_content_hash(chapter, "Book") != before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Guiltythree - Shadow Slave 001-100 (2022) - libgen.li", "Shadow Slave"),
        ("Shadow Slave 001-100", "Shadow Slave"),
        ("Shadow_Slave 001\N{EN DASH}100", "Shadow Slave"),
        # A year alone does not prove "Author - Title" order, so both parts stay.
        ("Some Author - Some Book (2022)", "Some Author - Some Book"),
        ("Catch-22 - Joseph Heller (1961)", "Catch-22 - Joseph Heller"),
        ("Title - Author (2020)", "Title - Author"),
        # An author is dropped only when a site name or a chapter range shows the pattern.
        ("Some Author - Some Book 001-100", "Some Book"),
        ("Some Author - Some Book - libgen.li", "Some Book"),
        ("Some Book (2022)", "Some Book"),
        ("Some Book - libgen.li", "Some Book"),
        # Real titles that merely look like noise must survive.
        ("Dune - Part 1", "Dune - Part 1"),
        ("Catch-22", "Catch-22"),
        ("1984", "1984"),
        ("Europe 1914-1918", "Europe 1914-1918"),
        ("Europe 1914-1918 (2005)", "Europe 1914-1918"),
        ("Shadow Slave 101-200 (2022)", "Shadow Slave"),
    ],
)
def test_clean_book_title_strips_download_noise_but_keeps_the_title(raw, expected):
    assert clean_book_title(raw) == expected


@pytest.mark.parametrize("raw", ["001-100", "(2022)", "libgen.li", "2022 - 2023"])
def test_clean_book_title_never_returns_empty_when_everything_looks_like_noise(raw):
    assert clean_book_title(raw) == raw


def test_build_prompt_states_the_negative_constraints_inline():
    prompt = build_prompt("Book", _chapter()).lower()

    for phrase in ("no text", "no letters", "no words", "no watermark", "no logo", "no signature"):
        assert phrase in prompt
    assert "no ui" in prompt
    assert "no border" in prompt
    assert "no frame" in prompt


def test_build_prompt_uses_the_cleaned_title_and_chapter_number_and_title():
    chapter = _chapter(index=2, number=1, title="Chapter 1: Nightmare Begins", text="Sunny woke.")

    prompt = build_prompt("Guiltythree - Shadow Slave 001-100 (2022) - libgen.li", chapter)

    assert "Shadow Slave" in prompt
    assert "001-100" not in prompt
    assert "libgen" not in prompt
    assert "chapter 1," in prompt
    assert "Nightmare Begins" in prompt
    assert "Chapter 1: Nightmare" not in prompt


def test_build_prompt_names_no_chapter_number_for_front_matter():
    chapter = _chapter(index=1, number=0, title="SYNOPSIS")

    prompt = build_prompt("Book", chapter)

    assert "the section called SYNOPSIS." in prompt
    assert "chapter 0" not in prompt


def test_build_prompt_opening_has_no_words_that_invite_lettering():
    prompt = build_prompt("Book", _chapter(number=3, title="The Gate"))

    opening = prompt.split("Scene")[0].lower()
    for word in ("webnovel", "key art", "cover", "titled"):
        assert word not in opening


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Chapter 1: Nightmare Begins", "Nightmare Begins"),
        ("Chapter IV: Hunters", "Hunters"),
        ("Ch. 7 - The Gate", "The Gate"),
        ("Child of the Storm", "Child of the Storm"),
        ("Chill Winds", "Chill Winds"),
        ("Chapter Dim Light", "Chapter Dim Light"),
        ("Ch. ill omen", "Ch. ill omen"),
        ("Chapter I Am Here", "Chapter I Am Here"),
        ("Chapter 12", "Chapter 12"),
    ],
)
def test_build_prompt_removes_only_a_real_chapter_prefix_from_the_chapter_title(title, expected):
    prompt = build_prompt("Book", _chapter(number=3, title=title))

    assert f"chapter 3, called {expected}." in prompt


def test_build_prompt_calls_a_blank_book_title_an_untitled_book():
    prompt = build_prompt("   ", _chapter())

    assert "a scene from an untitled novel," in prompt
    assert "the novel  " not in prompt


def test_build_prompt_caps_very_long_titles_on_a_word_boundary():
    long_title = "word " * 100
    chapter = _chapter(number=2, title=long_title)

    prompt = build_prompt(long_title, chapter)

    assert "word " * 30 not in prompt
    assert "a scene from the novel " + ("word " * 24).strip() + "," in prompt


def test_build_prompt_strips_bidi_controls_and_unicode_line_separators():
    hostile = "Ti" + chr(0x202E) + "tle" + chr(0x2066) + "x" + chr(0x200F) + chr(0x2028) + "y"
    chapter = _chapter(title=hostile, text="a" + chr(0x2029) + "b " + chr(0x202A) + "c")

    prompt = build_prompt(hostile, chapter)

    for code in (0x202E, 0x2066, 0x200F, 0x2028, 0x2029, 0x202A):
        assert chr(code) not in prompt


def test_chapter_content_hash_changes_when_the_book_title_changes():
    chapter = _chapter(number=1)

    assert chapter_content_hash(chapter, "Book One") != chapter_content_hash(chapter, "Book Two")


def test_chapter_content_hash_ignores_title_noise_that_the_prompt_drops():
    chapter = _chapter(number=1)

    assert chapter_content_hash(chapter, "Shadow Slave 001-100") == chapter_content_hash(
        chapter, "Shadow Slave"
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"rest", ("jpg", "image/jpeg")),
        (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"rest", ("png", "image/png")),
        (b"RIFF" + bytes(4) + b"WEBPVP8 ", ("webp", "image/webp")),
        (b"not an image", ("png", "image/png")),
        (b"", ("png", "image/png")),
    ],
)
def test_image_file_type_is_sniffed_from_the_bytes(data, expected):
    assert image_file_type(data) == expected


def test_build_prompt_flattens_quotes_and_newlines_in_untrusted_epub_text():
    chapter = _chapter(title='Say "hi"\n', text='"Run!" he said.\nThen {silence} [fell].')

    prompt = build_prompt('Bo"ok', chapter)

    assert "\n" not in prompt
    assert '"' not in prompt
    assert "{" not in prompt
    assert "[" not in prompt
    assert "Run! he said. Then silence fell." in prompt


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
