"""Shared helpers for the attribution evaluation scripts."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from book2audio.epub import parse_epub
from book2audio.models import Chapter, SegmentKind
from book2audio.text import segment_paragraphs

import scoring

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")  # gitignored: downloaded EPUBs
SHEETS_DIR = os.path.join(HERE, "sheets")
GOLD_DIR = os.path.join(HERE, "gold")
RESULTS_DIR = os.path.join(HERE, "results")
BOOKS_JSON = os.path.join(HERE, "books.json")


@dataclass
class DialogueLine:
    book: str
    chapter: Chapter
    position: int  # index into chapter.segments
    ordinal: int  # 1-based among the chapter's dialogue segments
    id: str


def load_books_manifest() -> dict:
    with open(BOOKS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def save_books_manifest(manifest: dict) -> None:
    with open(BOOKS_JSON, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def epub_path(book: dict) -> str:
    return os.path.join(DATA_DIR, book["file"])


def load_chapters(path: str) -> list[Chapter]:
    """Parse and segment an EPUB exactly the way the pipeline does."""
    with tempfile.TemporaryDirectory() as cover_dir:
        _metadata, parsed = parse_epub(path, cover_dir=cover_dir)
    return [
        Chapter(index=pc.index, title=pc.title, segments=segment_paragraphs(pc.paragraphs))
        for pc in parsed
    ]


def dialogue_lines(book: str, chapters: list[Chapter]) -> list[DialogueLine]:
    lines: list[DialogueLine] = []
    for chapter in chapters:
        ordinal = 0
        for position, seg in enumerate(chapter.segments):
            if seg.kind != SegmentKind.DIALOGUE:
                continue
            ordinal += 1
            lines.append(
                DialogueLine(
                    book=book,
                    chapter=chapter,
                    position=position,
                    ordinal=ordinal,
                    id=scoring.line_id(chapter.index, ordinal, seg.text),
                )
            )
    return lines
