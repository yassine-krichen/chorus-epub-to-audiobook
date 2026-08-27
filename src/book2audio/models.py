"""Shared domain models for the whole pipeline.

These are deliberately plain dataclasses: every stage (parse -> segment ->
attribute -> synthesize -> assemble) reads and writes these, so keeping them
free of behavior keeps the stages decoupled and independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SegmentKind(str, Enum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"
    # A hard scene break (``***``). Carries no text, only a longer pause.
    SCENE_BREAK = "scene_break"


# Canonical speaker name used for all non-dialogue prose.
NARRATOR = "Narrator"


@dataclass
class Segment:
    """A single stretch of text spoken by one voice.

    ``speaker`` is the resolved character name (or :data:`NARRATOR`). It starts
    as ``None`` for dialogue and is filled in by the attribution stage.
    """

    kind: SegmentKind
    text: str
    speaker: str | None = None


@dataclass
class Chapter:
    index: int  # 1-based position in reading order
    title: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)


@dataclass
class BookMetadata:
    title: str
    author: str
    language: str = "en"
    # Absolute path to extracted cover image, if the EPUB had one.
    cover_path: str | None = None


@dataclass
class Book:
    metadata: BookMetadata
    chapters: list[Chapter] = field(default_factory=list)
