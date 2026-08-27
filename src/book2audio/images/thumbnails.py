"""Per-chapter cover-thumbnail generation, on top of the freellmapi client.

Kept separate from ``freellmapi.py`` so the transport client stays generic
and this module owns book2audio's own prompt-building and caching concerns.
"""

from __future__ import annotations

import hashlib
import logging

from book2audio.images.freellmapi import ImageGenError, generate_image
from book2audio.models import Chapter

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 400


def _excerpt(chapter: Chapter) -> str:
    """A short, stable slice of a chapter's text — enough to steer an image
    prompt without sending (or hashing) the whole chapter."""
    text = " ".join(seg.text for seg in chapter.segments if seg.text)
    return text[:_EXCERPT_CHARS]


def build_prompt(book_title: str, chapter: Chapter) -> str:
    """Build a short, safe image-gen prompt from the book title, chapter
    title, and a short excerpt of the chapter's text."""
    excerpt = _excerpt(chapter)
    prompt = (
        f"Book cover style illustration for a chapter of '{book_title}', "
        f"titled '{chapter.title}'. Scene inspired by: {excerpt}"
    )
    return prompt


def chapter_content_hash(chapter: Chapter) -> str:
    """Fingerprint only what affects the *image* — title and excerpt.

    A scoped-down sibling of ``Pipeline._chapter_hash``: voice, speed, and
    music settings are irrelevant to a picture, so they're deliberately
    excluded here to avoid needless thumbnail regeneration on unrelated
    config changes.
    """
    blob = f"{chapter.title}␟{_excerpt(chapter)}".encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def try_generate_thumbnail(book_title: str, chapter: Chapter) -> bytes | None:
    """Generate a chapter thumbnail, or ``None`` on any failure.

    Must never raise: thumbnails are a best-effort enhancement, not a reason
    to fail an otherwise-successful render.
    """
    prompt = build_prompt(book_title, chapter)
    try:
        return generate_image(prompt)
    except ImageGenError as exc:
        logger.warning("Thumbnail generation failed for chapter %d: %s", chapter.index, exc)
        return None
