"""Look inside an uploaded EPUB before a job exists: its chapters, for the chapter picker.

The wizard uploads the book straight to storage, so the API has not seen it yet. This
downloads it once, reads the chapter list with the same parser (and the same numbering
rules) a render uses, and returns only what the picker needs. Nothing is stored.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
import zipfile
from xml.etree.ElementTree import ParseError

from fastapi import HTTPException, status

from book2audio.api.jobs.schemas import BookInspection, ChapterInfo
from book2audio.epub import parse_epub
from book2audio.storage.r2 import download_file, object_size

logger = logging.getLogger(__name__)

# The wizard's own upload cap (see routes.MAX_UPLOAD_BYTES); storage does not enforce it.
MAX_INSPECT_BYTES = 50 * 1024 * 1024
# An EPUB is a zip, so a small file can hold a huge one. Refuse before parsing.
_MAX_ZIP_ENTRIES = 5_000
_MAX_UNPACKED_BYTES = 400 * 1024 * 1024
_MAX_TITLE_CHARS = 200


def upload_prefix(user_id: uuid.UUID) -> str:
    """Where ``POST /jobs/upload-url`` puts a user's uploads."""
    return f"uploads/{user_id}/"


def inspect_uploaded_epub(user_id: uuid.UUID, object_key: str) -> BookInspection:
    """Chapters of an EPUB the user uploaded. 404 when it is not theirs or not there."""
    # Same answer for "not yours" and "does not exist", like the job endpoints.
    if not object_key.startswith(upload_prefix(user_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    size = object_size(object_key)
    if size is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    if size > MAX_INSPECT_BYTES:
        raise HTTPException(413, "The file is too large to read")

    with tempfile.TemporaryDirectory() as scratch:
        epub_path = f"{scratch}/book.epub"
        download_file(object_key, epub_path)
        return inspect_epub(epub_path, scratch)


def inspect_epub(epub_path: str, scratch_dir: str) -> BookInspection:
    """Chapter list of the EPUB at ``epub_path``; 422 when it is not a usable EPUB."""
    try:
        _refuse_zip_bomb(epub_path)
        metadata, chapters = parse_epub(epub_path, cover_dir=scratch_dir)
    except (zipfile.BadZipFile, ParseError, ValueError, KeyError, OSError):
        logger.info("inspect.unusable_epub", exc_info=True)
        raise HTTPException(422, "This file could not be read as an EPUB") from None
    return BookInspection(
        title=metadata.title[:_MAX_TITLE_CHARS],
        author=metadata.author[:_MAX_TITLE_CHARS],
        chapters=[
            ChapterInfo(
                index=chapter.index,
                number=chapter.number,
                title=chapter.title[:_MAX_TITLE_CHARS],
                front_matter=chapter.front_matter,
                word_count=sum(len(paragraph.split()) for paragraph in chapter.paragraphs),
            )
            for chapter in chapters
        ],
    )


def _refuse_zip_bomb(epub_path: str) -> None:
    with zipfile.ZipFile(epub_path) as archive:
        entries = archive.infolist()
        if len(entries) > _MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > _MAX_UNPACKED_BYTES:
            raise ValueError("EPUB archive is too large to read")
