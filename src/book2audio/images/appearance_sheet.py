"""The main character's appearance over the book, saved in the work dir.

A character's look can change as the story goes on, so the sheet is a timeline,
not a single record: each entry holds from one book chapter number on, and the
entry in force for a chapter is the latest one that starts at or before it (ties
go to the entry learned from the later chapter). Entries are only ever added,
never replaced, so a chapter can always be shown the sheet as it stood before it
was read. A chapter's own text always wins over the sheet (the brief prompt
says so); the sheet only carries the look forward to chapters that do not
describe it again.

Only the main character is tracked (the top speaker by line count). The file is
an optimisation, never a source of truth: a missing or corrupt one starts empty.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Mapping

from book2audio.images.scene_brief import (
    FIELD_MAX_CHARS,
    AppearanceEntry,
    AppearanceUpdate,
)
from book2audio.images.thumbnails import cap_at_word, prompt_safe

logger = logging.getLogger(__name__)

SHEET_FILENAME = "appearance_sheet.json"
_SHEET_VERSION = 1
# A sheet file is untrusted input (work dirs are restored from storage): bound it.
_MAX_ENTRIES = 500


class AppearanceSheet:
    def __init__(self, path: str | None, main_character: str | None) -> None:
        self.path = path
        self.main_character = main_character
        self._entries: list[AppearanceEntry] = []
        self.write_error: str | None = None
        if path and main_character:
            self._entries = _load_entries(path, main_character)

    @property
    def entries(self) -> tuple[AppearanceEntry, ...]:
        return tuple(self._entries)

    def current_for(
        self, number: int, *, known_before_index: int | None = None
    ) -> AppearanceEntry | None:
        """The entry in force for book chapter ``number``: the one with the largest
        ``from_chapter`` at or before it.

        ``known_before_index`` hides entries learned from that chapter (by parser
        position) or later, giving the sheet as it stood before the chapter was
        read. A re-run then sees the same input as the first run, even though the
        saved sheet has since learned this chapter's own update.
        """
        candidates = [
            entry
            for entry in self._entries
            if entry.from_chapter <= number
            and (known_before_index is None or entry.source_chapter_index < known_before_index)
        ]
        return max(candidates, key=_precedence, default=None)

    def apply_update(
        self, update: AppearanceUpdate, *, chapter_number: int, chapter_index: int
    ) -> bool:
        """Record a look change reported by chapter ``chapter_number``.

        Returns whether the sheet changed. Rejected: another character than the
        main one, and a ``from_chapter`` after the reporting chapter (a chapter
        cannot know the future). The update is added as a new entry, never over an
        old one: for the same ``from_chapter`` the entry learned from the later
        chapter wins, but chapters read earlier still see the sheet as it was.
        An update repeating the look already in force for that ``from_chapter``
        changes nothing.
        """
        if not self.main_character or update.character.casefold() != self.main_character.casefold():
            return False
        if update.from_chapter > chapter_number:
            logger.info(
                "Ignored appearance update from chapter %d reported by chapter %d",
                update.from_chapter,
                chapter_number,
            )
            return False

        same_start = [e for e in self._entries if e.from_chapter == update.from_chapter]
        newest = max(same_start, key=_precedence, default=None)
        if newest is not None and newest.description == update.description:
            return False
        entry = AppearanceEntry(
            character=self.main_character,
            from_chapter=update.from_chapter,
            description=update.description,
            source_chapter_index=chapter_index,
        )
        self._entries.append(entry)
        self._entries.sort(key=_precedence)
        self._save()
        return True

    def _save(self) -> None:
        if not self.path:
            return
        payload = {
            "version": _SHEET_VERSION,
            "main_character": self.main_character,
            "entries": [
                {
                    "character": e.character,
                    "from_chapter": e.from_chapter,
                    "description": e.description,
                    "source_chapter_index": e.source_chapter_index,
                }
                for e in self._entries
            ],
        }
        self.write_error = write_json_atomically(self.path, payload)


def _load_entries(path: str, main_character: str) -> list[AppearanceEntry]:
    loaded = read_json_object(path)
    # A different main character means a different cast: the old look is stale.
    saved_main = str(loaded.get("main_character", "")) if loaded else ""
    if loaded is None or saved_main.casefold() != main_character.casefold():
        return []
    raw_entries = loaded.get("entries")
    if not isinstance(raw_entries, list):
        return []
    entries = []
    for raw in raw_entries[:_MAX_ENTRIES]:
        entry = _entry_from_json(raw, main_character)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=_precedence)


def _precedence(entry: AppearanceEntry) -> tuple[int, int]:
    return entry.from_chapter, entry.source_chapter_index


def _entry_from_json(raw: object, main_character: str) -> AppearanceEntry | None:
    """One saved entry, re-sanitised like model output: the file may have been edited."""
    if not isinstance(raw, dict):
        return None
    description = raw.get("description")
    from_chapter, source = raw.get("from_chapter"), raw.get("source_chapter_index")
    if not isinstance(description, str):
        return None
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (from_chapter, source)):
        return None
    if from_chapter < 0:
        return None
    clean = cap_at_word(prompt_safe(description), FIELD_MAX_CHARS)
    if not clean:
        return None
    return AppearanceEntry(main_character, from_chapter, clean, source)


def read_json_object(path: str) -> Mapping[str, object] | None:
    """The JSON object in ``path``, or None when it is missing, unreadable or not an object."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError, RecursionError):  # deeply nested JSON raises RecursionError
        return None
    return loaded if isinstance(loaded, dict) else None


def write_json_atomically(path: str, payload: object) -> str | None:
    """Write ``payload`` as JSON via a temp file. Returns an error message, or None.

    The temp name is unique per process and call, so two runs sharing a work
    dir cannot clobber each other's half-written file.
    """
    tmp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return str(exc)
    return None
