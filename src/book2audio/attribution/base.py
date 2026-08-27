"""The attribution interface every strategy implements."""

from __future__ import annotations

from typing import Protocol

from book2audio.models import Chapter


class Attributor(Protocol):
    """Assigns a ``speaker`` to every segment across a book's chapters.

    Implementations mutate each :class:`~book2audio.models.Segment` in place and
    return the ordered set of distinct character names discovered (excluding the
    narrator), so the caller can cast voices and let the user review characters.
    """

    def attribute(self, chapters: list[Chapter]) -> list[str]: ...
