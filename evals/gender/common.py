"""Shared helpers for the gender evaluation: parsing, slices, gold, voice buckets."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from book2audio.epub import parse_epub
from book2audio.models import NARRATOR, Chapter
from book2audio.text import segment_paragraphs

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GOLD_DIR = os.path.join(HERE, "gold")
LOCAL_DIR = os.path.join(HERE, "local")  # gitignored: verbatim book text
EPUB_PATH = os.path.join(
    REPO, "epubs", "Guiltythree - Shadow Slave 001-100 (2022) - libgen.li.epub"
)
BOOK_LABEL = "Shadow Slave 001-100 (first volume)"


@dataclass(frozen=True)
class Slice:
    first: int  # book chapter numbers, inclusive
    last: int


SLICES = {
    "ch01-10": Slice(1, 10),  # all male speakers: cannot test female detection
    "ch36-43": Slice(36, 43),  # Nephis and Cassie speak in every chapter
}


def load_book(path: str = EPUB_PATH) -> list[Chapter]:
    """Parse and segment the EPUB the way the pipeline does (book numbers included)."""
    with tempfile.TemporaryDirectory() as cover_dir:
        _metadata, parsed = parse_epub(path, cover_dir=cover_dir)
    return [
        Chapter(
            index=pc.index,
            title=pc.title,
            segments=segment_paragraphs(pc.paragraphs),
            number=pc.number,
            front_matter=pc.front_matter,
        )
        for pc in parsed
    ]


def chapters_in(chapters: list[Chapter], spec: Slice) -> list[Chapter]:
    return [c for c in chapters if spec.first <= c.number <= spec.last and not c.front_matter]


@dataclass(frozen=True)
class GoldCharacter:
    name: str
    aliases: tuple[str, ...]
    gender: str  # "m" | "f" | "unknown"
    approx_dialogue_lines: int


class Gold:
    """Character gold for one slice, looked up by name or alias (case-insensitive)."""

    def __init__(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.characters = [
            GoldCharacter(
                name=c["name"],
                aliases=tuple(c["aliases"]),
                gender=c["gender"],
                approx_dialogue_lines=c["approx_dialogue_lines"],
            )
            for c in payload["characters"]
        ]
        self._by_name = {}
        for character in self.characters:
            for key in (character.name, *character.aliases):
                self._by_name.setdefault(key.casefold(), character)

    def find(self, name: str) -> GoldCharacter | None:
        return self._by_name.get(name.casefold())


def load_gold(slice_name: str) -> Gold:
    return Gold(os.path.join(GOLD_DIR, f"{slice_name}.json"))


def voice_bucket(voice: str, narrator_voice: str) -> str:
    """``m`` / ``f`` from the Kokoro voice prefix, or ``narrator`` for the narrator voice."""
    if voice == narrator_voice:
        return "narrator"
    return voice[1]


def is_narrator(speaker: str | None) -> bool:
    return speaker in (None, NARRATOR)
