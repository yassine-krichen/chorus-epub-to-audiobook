"""Edits a user makes to the detected cast: folding one character into another.

Speaker detection sometimes splits one person into two names ("Changing Star" and
"Nephis") or gives a minor speaker's lines to a main character. The cast review lets
the user say "these are the same person", or "the narrator reads this one", and the
pipeline applies that after attribution, so it never costs another model call.

An alias maps the name as detected to the name that takes over its lines. The target
may be ``NARRATOR``, which sends the lines back to the narrator voice.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from book2audio.models import Chapter, SegmentKind

# Job status of a hosted job parked after attribution and casting, until the user
# confirms the cast (or the review expires).
AWAITING_CAST_REVIEW: Final = "awaiting_cast_review"

CHARACTER_ALIAS_MAX_ENTRIES = 100
CHARACTER_ALIAS_MAX_NAME_LENGTH = 80


def validate_character_aliases(aliases: Mapping[str, str]) -> None:
    """Raise ``ValueError`` unless ``aliases`` is a small, chain-free ``name -> name`` map."""
    if len(aliases) > CHARACTER_ALIAS_MAX_ENTRIES:
        raise ValueError(f"at most {CHARACTER_ALIAS_MAX_ENTRIES} character merges are allowed")
    for source, target in aliases.items():
        for name in (source, target):
            if not name.strip() or len(name) > CHARACTER_ALIAS_MAX_NAME_LENGTH:
                raise ValueError(
                    "character merge names must be 1 to "
                    f"{CHARACTER_ALIAS_MAX_NAME_LENGTH} characters"
                )
        if source.strip().lower() == target.strip().lower():
            raise ValueError(f"{source!r} cannot be merged into itself")
        # A chain (A into B, B into C) would leave A's lines on a character that
        # no longer exists, so a target must stay a character of its own.
        if target in aliases:
            raise ValueError(
                f"{target!r} is merged into another character, so {source!r} cannot join it"
            )


def merge_characters(chapters: list[Chapter], aliases: Mapping[str, str]) -> dict[str, str]:
    """Give each aliased speaker's lines to its target; return the merges that applied.

    A name that speaks no line in ``chapters`` (a stale review, or a character outside
    the selected chapters) is skipped, so the result lists only what really changed.
    """
    applied: dict[str, str] = {}
    for chapter in chapters:
        for segment in chapter.segments:
            if segment.kind != SegmentKind.DIALOGUE or segment.speaker is None:
                continue
            source = segment.speaker
            target = aliases.get(source)
            if target is not None:
                segment.speaker = target
                applied[source] = target
    return applied
