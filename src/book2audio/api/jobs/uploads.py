"""Who owns an uploaded EPUB: the shape of upload keys and the guard that enforces it.

``POST /jobs/upload-url`` hands out keys of the form
``uploads/<user id>/<random id>-<file name>``. Every endpoint that reads an upload (job
creation, the chapter picker) must call :func:`require_owned_upload`, so a user can only
ever point the worker or the API at a file they uploaded themselves.
"""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException, status

MAX_UPLOAD_KEY_LENGTH = 600
# The largest EPUB the wizard accepts. The upload URL is signed for the exact size the client
# declares (up to this), so storage refuses a PUT of any other length.
MAX_EPUB_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_FILENAME_LENGTH = 255

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# No path separators or control characters in the file name, so a key can never name
# anything but one object directly under the user's own prefix.
_UNSAFE_FILENAME = re.compile(r"[/\\\x00-\x1f\x7f]")
_OWN_UPLOAD = re.compile(
    rf"uploads/(?P<owner>{_UUID})/{_UUID}-(?P<filename>[^/\\\x00-\x1f\x7f]{{1,{MAX_UPLOAD_FILENAME_LENGTH}}})"
)


def validate_upload_filename(filename: str) -> str:
    """Return ``filename`` if it can safely be part of an upload key, else raise ``ValueError``."""
    if _UNSAFE_FILENAME.search(filename) or filename.strip(" .") == "":
        raise ValueError("file names cannot contain slashes, control characters or be only dots")
    return filename


def upload_key(user_id: uuid.UUID, filename: str) -> str:
    return f"uploads/{user_id}/{uuid.uuid4()}-{filename}"


def require_owned_upload(user_id: uuid.UUID, object_key: str) -> None:
    """Raise 404 unless ``object_key`` is an upload key that belongs to ``user_id``.

    The same 404 covers "someone else's upload", "not an upload key at all" (a job's
    part file, a voice sample) and "does not exist", so it reveals nothing about
    other users' files.
    """
    match = _OWN_UPLOAD.fullmatch(object_key)
    if match is None or match.group("owner") != str(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    try:
        validate_upload_filename(match.group("filename"))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found") from None
