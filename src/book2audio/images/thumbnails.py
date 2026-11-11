"""Per-chapter cover-thumbnail generation, on top of the freellmapi client.

Kept separate from ``freellmapi.py`` so the transport client stays generic
and this module owns book2audio's own prompt-building and caching concerns.
"""

from __future__ import annotations

import hashlib
import logging
import re

from book2audio.images.freellmapi import ImageGenError, generate_image
from book2audio.models import Chapter

logger = logging.getLogger(__name__)

# Bump whenever the prompt template changes: it is part of the content hash, so
# thumbnails cached under an older template are regenerated on the next run.
TEMPLATE_VERSION = "template-v2"

_EXCERPT_CHARS = 400
_EXCERPT_MIN_SENTENCE_CHARS = 150
# Epub titles are untrusted; a pathological one must not swamp the prompt.
_TITLE_MAX_CHARS = 120

# En dash and em dash, spelled by name so the source stays plain ASCII.
_DASHES = "\N{EN DASH}\N{EM DASH}"

# Epub metadata titles often carry download-site noise, e.g.
# "Author - Title 001-100 (2022) - libgen.li". These patterns remove the noise,
# never the title itself.
_TITLE_SEPARATOR = " - "
_SOURCE_SEGMENT = re.compile(
    r"^\s*(?:libgen|z-?lib|anna'?s[ -]archive|epub|ebook|mobi|pdf)", re.IGNORECASE
)
_PAREN_NOISE = re.compile(
    r"[(\[]\s*(?:(?:19|20)\d{2}|(?:libgen|z-?lib)[^)\]]*)\s*[)\]]", re.IGNORECASE
)
# A chapter range ("001-100") only counts at the very end of the title.
_TRAILING_RANGE = re.compile(rf"\b(\d{{1,4}})\s*[-{_DASHES}]\s*(\d{{1,4}})\s*$")
# "Europe 1914-1918" is a real title, not a chapter range.
_YEAR = re.compile(r"(?:19|20)\d{2}$")
# Roman numerals count only when uppercase and followed by a separator or the
# end, so "Chapter Dim Light" and "Ch. ill omen" are not read as numbered.
_CHAPTER_PREFIX = re.compile(
    rf"^\s*(?:chapter|ch\b\.?)\s*"
    rf"(?:\d+\b|(?-i:[IVXLCDM]+)(?=\s*[:.\-{_DASHES}]|\s*$))"
    rf"\s*[:.\-{_DASHES}]?\s*",
    re.IGNORECASE,
)
# Quote, bracket and escape characters that could break out of, or confuse, a
# plain-sentence prompt. Includes typographic double quotes and guillemets.
_PROMPT_UNSAFE = re.compile(
    "[\"\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
    "\N{DOUBLE LOW-9 QUOTATION MARK}\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}`{}<>\\[\\]\\\\]"
)
# C0 controls, DEL, bidi marks/embeddings/isolates and the Unicode line and
# paragraph separators: invisible in a log or a picture caption, but able to
# reorder or split the text. Same set the filename sanitiser removes.
_INVISIBLE_CODEPOINTS = (
    [0x200E, 0x200F, 0x2028, 0x2029]
    + list(range(0x202A, 0x202F))
    + list(range(0x2066, 0x206A))
)
_CONTROL_CHARS = re.compile(
    "[\x00-\x1f\x7f" + "".join(chr(code) for code in _INVISIBLE_CODEPOINTS) + "]"
)

_JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
_PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47])


def clean_book_title(raw: str) -> str:
    """Strip download-site noise from an epub title so it names the book.

    "Guiltythree - Shadow Slave 001-100 (2022) - libgen.li" -> "Shadow Slave".
    Years in parentheses, a trailing chapter range and site names are removed.
    A leading "Author - " segment is dropped only when a site name or a chapter
    range shows the title came from a download file; "Catch-22 - Joseph Heller
    (1961)" keeps both parts. Conservative: if cleaning would leave nothing,
    the trimmed raw title is returned instead.
    """
    stripped = " ".join(raw.replace("_", " ").split())
    segments = stripped.split(_TITLE_SEPARATOR)

    kept = [seg for seg in segments if not _SOURCE_SEGMENT.match(seg)]
    removed_source = len(kept) < len(segments)
    kept = [_PAREN_NOISE.sub(" ", seg).strip() for seg in kept]

    had_range = False
    if kept:
        kept[-1], had_range = _strip_trailing_range(kept[-1])
    kept = [seg for seg in kept if seg]

    if len(kept) >= 2 and (removed_source or had_range):
        kept = kept[1:]

    cleaned = " ".join(_TITLE_SEPARATOR.join(kept).split()).strip(f" -{_DASHES}:,")
    return cleaned or stripped


def _strip_trailing_range(text: str) -> tuple[str, bool]:
    match = _TRAILING_RANGE.search(text)
    if match is None:
        return text, False
    if _YEAR.match(match.group(1)) and _YEAR.match(match.group(2)):
        return text, False
    return text[: match.start()].rstrip(), True


def _chapter_title_without_number(title: str) -> str:
    """"Chapter 1: Nightmare Begins" -> "Nightmare Begins". Falls back to the
    full title when nothing would remain (e.g. a bare "Chapter 1")."""
    return _CHAPTER_PREFIX.sub("", title, count=1).strip() or title.strip()


def prompt_safe(text: str) -> str:
    """Flatten untrusted epub text to plain words: no newlines, invisible
    control characters, or quote/bracket characters that could break the prompt."""
    text = _CONTROL_CHARS.sub(" ", text)
    text = _PROMPT_UNSAFE.sub("", text)
    return " ".join(text.split())


def cap_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    word_end = cut.rfind(" ")
    return cut[:word_end] if word_end > 0 else cut


def _excerpt(chapter: Chapter) -> str:
    """A short, stable slice of a chapter's text, enough to steer an image
    prompt without sending (or hashing) the whole chapter."""
    text = " ".join(seg.text for seg in chapter.segments if seg.text)
    if len(text) <= _EXCERPT_CHARS:
        return text
    # Prefer ending on a whole sentence, then a whole word: a prompt that stops
    # mid-word reads as a fragment the image model may latch onto.
    cut = text[:_EXCERPT_CHARS]
    sentence_end = max(cut.rfind(mark) for mark in ".!?")
    if sentence_end >= _EXCERPT_MIN_SENTENCE_CHARS:
        return cut[: sentence_end + 1]
    return cap_at_word(cut, _EXCERPT_CHARS)


def build_prompt(book_title: str, chapter: Chapter, *, scene: str | None = None) -> str:
    """Fill the engineered illustration template (no LLM call).

    freellmapi's image endpoint has no negative-prompt field, so the
    "no text / watermark" constraints are spelled out inline. ``scene`` (the
    enhanced level's already-sanitised scene description) replaces the raw
    excerpt; the style, composition and no-text constraints stay the same.
    """
    title = cap_at_word(prompt_safe(clean_book_title(book_title)), _TITLE_MAX_CHARS)
    book = f"the novel {title}" if title else "an untitled novel"

    chapter_title = cap_at_word(
        prompt_safe(_chapter_title_without_number(chapter.title)), _TITLE_MAX_CHARS
    )
    # Number 0 is front matter or a prologue: there is no chapter number to name.
    if chapter.number > 0:
        subject = f"chapter {chapter.number}, called {chapter_title}"
    else:
        subject = f"the section called {chapter_title}"

    if scene is None:
        excerpt = prompt_safe(_excerpt(chapter))
        if excerpt and excerpt[-1] not in ".!?":
            excerpt += "..."
        scene_sentence = f"Scene inspired by this passage: {excerpt} "
    else:
        scene_sentence = f"Scene: {scene} "

    return (
        # No "key art", "cover", "title" or site names here: in a real test the image
        # model painted the opening words of the prompt onto the picture as lettering.
        f"Digital painting in the style of anime light-novel illustration: a scene from "
        f"{book}, {subject}. "
        f"{scene_sentence}"
        "Style: painterly digital illustration, manhwa-inspired character rendering, "
        "rich detail, saturated fantasy color palette. "
        "Lighting and mood: dramatic cinematic lighting, deep shadows, one strong rim light, "
        "tense atmosphere. "
        "Composition: square format, a single focal subject placed on a rule-of-thirds point, "
        "strong silhouette, clear foreground and background depth. "
        "No text, no letters, no words, no numbers, no captions, no watermark, no logo, "
        "no signature, no UI, no border, no frame."
    )


def chapter_content_hash(chapter: Chapter, book_title: str) -> str:
    """Fingerprint only what affects the *image*: the template version plus the
    prompt built from the book title, chapter number, chapter title and excerpt.

    A scoped-down sibling of ``Pipeline._chapter_hash``: voice, speed, and
    music settings are irrelevant to a picture, so they're deliberately
    excluded here to avoid needless thumbnail regeneration on unrelated
    config changes. ``chapter.index`` is excluded too; only ``number``, which
    the prompt names, counts.
    """
    blob = f"{TEMPLATE_VERSION}\x1f{build_prompt(book_title, chapter)}".encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def image_file_type(data: bytes) -> tuple[str, str]:
    """Return ``(extension, content_type)`` for the image bytes, sniffed from
    their magic numbers. The provider returns JPEG today, so trusting a fixed
    ".png" would mislabel the file. Unrecognised bytes fall back to PNG, the
    format this code originally assumed."""
    if data.startswith(_JPEG_MAGIC):
        return "jpg", "image/jpeg"
    if data.startswith(_PNG_MAGIC):
        return "png", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "png", "image/png"


def try_generate_thumbnail(
    book_title: str, chapter: Chapter, *, prompt: str | None = None
) -> bytes | None:
    """Generate a chapter thumbnail, or ``None`` on any failure.

    Must never raise: thumbnails are a best-effort enhancement, not a reason
    to fail an otherwise-successful render. ``prompt`` overrides the template
    prompt (the enhanced level passes its own).
    """
    if prompt is None:
        prompt = build_prompt(book_title, chapter)
    try:
        return generate_image(prompt)
    except ImageGenError as exc:
        logger.warning("Thumbnail generation failed for chapter %d: %s", chapter.index, exc)
        return None
