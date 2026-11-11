"""Look inside an uploaded EPUB before a job exists: its chapters, for the chapter picker.

The wizard uploads the book straight to storage, so the API has not seen it yet. This
downloads it once, reads the chapter list with the same parser (and the same numbering
rules) a render uses, and returns only what the picker needs. Nothing is stored.

The file is untrusted and is parsed inside the API process, so every limit here is
checked before the parser runs: download size (while streaming, pinned to one version
of the object), archive entry count, per-entry and total unpacked size. Python's zipfile
never reads more bytes from an entry than its declared size, so those declared sizes
are real limits. The parser itself is not sandboxed or run in a subprocess: a portable
memory limit is not available (the API runs on Linux, development on Windows), so the
caps and the per-user rate limit are what bound the work.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
import zipfile

from botocore.exceptions import ClientError
from fastapi import HTTPException

from book2audio.api.jobs.schemas import BookInspection, ChapterInfo
from book2audio.api.jobs.uploads import require_owned_upload
from book2audio.epub import parse_epub
from book2audio.storage.r2 import download_capped

logger = logging.getLogger(__name__)

# The wizard's own upload cap (see routes.MAX_UPLOAD_BYTES); storage does not enforce it.
MAX_INSPECT_BYTES = 50 * 1024 * 1024
# An EPUB is a zip, so a small file can hold a huge one. Refuse before parsing.
_MAX_ZIP_ENTRIES = 5_000
_MAX_ENTRY_BYTES = 16 * 1024 * 1024
_MAX_UNPACKED_BYTES = 100 * 1024 * 1024
_MAX_TITLE_CHARS = 200

_UNREADABLE = "This file could not be read as an EPUB"


def inspect_uploaded_epub(user_id: uuid.UUID, object_key: str) -> BookInspection:
    """Chapters of an EPUB the user uploaded. 404 when it is not theirs or not there."""
    require_owned_upload(user_id, object_key)

    with tempfile.TemporaryDirectory() as scratch:
        epub_path = os.path.join(scratch, "book.epub")
        try:
            found = download_capped(object_key, epub_path, max_bytes=MAX_INSPECT_BYTES)
        except ValueError:
            raise HTTPException(413, "The file is too large to read") from None
        except ClientError:
            # Includes the object being replaced while it was read (failed ETag check).
            logger.warning("inspect.download_failed", exc_info=True)
            raise HTTPException(409, "The upload changed while it was being read. Try again.") from None
        if not found:
            raise HTTPException(404, "Upload not found")
        return inspect_epub(epub_path, scratch)


def inspect_epub(epub_path: str, scratch_dir: str) -> BookInspection:
    """Chapter list of the EPUB at ``epub_path``; 422 when it is not a usable EPUB."""
    try:
        _refuse_zip_bomb(epub_path)
        metadata, chapters = parse_epub(epub_path, cover_dir=scratch_dir)
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
    except Exception:  # noqa: BLE001 - an untrusted file can break the parser in any way
        logger.warning("inspect.unusable_epub", exc_info=True)
        raise HTTPException(422, _UNREADABLE) from None


def _refuse_zip_bomb(epub_path: str) -> None:
    with zipfile.ZipFile(epub_path) as archive:
        entries = archive.infolist()
        if len(entries) > _MAX_ZIP_ENTRIES:
            raise ValueError("EPUB archive has too many entries")
        if sum(e.file_size for e in entries) > _MAX_UNPACKED_BYTES:
            raise ValueError("EPUB archive unpacks to too much data")
        for entry in entries:
            if entry.file_size > _MAX_ENTRY_BYTES:
                raise ValueError(f"EPUB entry {entry.filename!r} unpacks to too much data")
            # Data that claims a size but has nothing stored is a forged header.
            if entry.file_size > 0 and entry.compress_size == 0:
                raise ValueError(f"EPUB entry {entry.filename!r} has a forged size")
