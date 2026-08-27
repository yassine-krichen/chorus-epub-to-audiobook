"""EPUB import: unpack an .epub into structured chapters + metadata."""

from book2audio.epub.parser import ParsedChapter, parse_epub

__all__ = ["parse_epub", "ParsedChapter"]
