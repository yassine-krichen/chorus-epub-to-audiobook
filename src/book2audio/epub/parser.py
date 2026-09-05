"""Parse a standard EPUB (2 or 3) into ordered chapters and metadata.

We read the spine to get reading order, pull each XHTML document's heading and
paragraphs, and extract the cover image. Only the standard library plus
BeautifulSoup is used so this stays dependency-light and works on any EPUB, not
just the one book we are targeting first.
"""

from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from book2audio.models import BookMetadata

logger = logging.getLogger(__name__)

# XML namespaces used across the OPF / container files.
_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Front-matter documents we never want narrated: cover pages, the scraper's
# "Information" dump of source URLs, nav/TOC, title/copyright pages. Matched
# against both the document's filename and its heading, since either may carry
# the signal (headings like "SHADOW SLAVE" on an info page won't, filenames do).
_SKIP = re.compile(
    r"(cover|information|table of contents|\bcontents\b|\bnav\b|\btoc\b"
    r"|titlepage|title[-_]?page|copyright)",
    re.I,
)

# Below this word count a document is treated as non-narratable front matter.
_MIN_CHAPTER_WORDS = 40


@dataclass
class ParsedChapter:
    """A chapter as it comes out of the EPUB: a title and raw paragraphs.

    Segmentation into narration/dialogue happens later, in the text stage.
    """

    index: int
    title: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class _ManifestItem:
    """A manifest entry: archive path plus its OPF ``properties`` attribute."""

    href: str
    properties: str = ""


@dataclass
class _SpineItem:
    """A spine ``<itemref>``: which manifest id, and its ``linear`` flag."""

    idref: str
    linear: str | None = None


def parse_epub(epub_path: str, cover_dir: str) -> tuple[BookMetadata, list[ParsedChapter]]:
    """Read ``epub_path`` and return its metadata and narratable chapters.

    ``cover_dir`` is where the cover image is written if one exists.
    Raises :class:`ValueError` if the file is not a usable EPUB.
    """
    with zipfile.ZipFile(epub_path) as zf:
        opf_path = _find_opf_path(zf)
        opf_root = ET.fromstring(zf.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)

        metadata = _read_metadata(opf_root)
        manifest = _read_manifest(opf_root, opf_dir)
        spine_items = _read_spine(opf_root)

        metadata.cover_path = _extract_cover(zf, opf_root, manifest, cover_dir)

        chapters: list[ParsedChapter] = []
        for spine_item in spine_items:
            # EPUB OPF spec: linear="no" marks non-linear content (e.g. footnote
            # popups) that isn't part of the main reading order.
            if spine_item.linear == "no":
                continue
            item = manifest.get(spine_item.idref)
            if item is None:
                continue
            # EPUB3 nav documents occasionally appear in the spine too.
            if "nav" in item.properties.split():
                continue
            try:
                html = zf.read(item.href).decode("utf-8", errors="replace")
            except KeyError:
                continue
            chapter = _parse_document(html, item.href, index=len(chapters) + 1)
            if chapter is not None:
                chapters.append(chapter)

    if not chapters:
        raise ValueError(f"No narratable chapters found in {epub_path!r}")
    return metadata, chapters


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//container:rootfile", _NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("EPUB is missing its OPF rootfile reference")
    return rootfile.get("full-path")  # type: ignore[return-value]


def _read_metadata(opf_root: ET.Element) -> BookMetadata:
    def first_text(tag: str, default: str) -> str:
        el = opf_root.find(f".//dc:{tag}", _NS)
        return el.text.strip() if el is not None and el.text else default

    return BookMetadata(
        title=first_text("title", "Untitled"),
        author=first_text("creator", "Unknown Author"),
        language=first_text("language", "en").split("_")[0].split("-")[0],
    )


def _read_manifest(opf_root: ET.Element, opf_dir: str) -> dict[str, _ManifestItem]:
    """Map manifest item id -> its archive path and OPF ``properties``."""
    manifest: dict[str, _ManifestItem] = {}
    for item in opf_root.findall(".//opf:manifest/opf:item", _NS):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest[item_id] = _ManifestItem(
                href=_join(opf_dir, href),
                properties=item.get("properties") or "",
            )
    return manifest


def _read_spine(opf_root: ET.Element) -> list[_SpineItem]:
    return [
        _SpineItem(idref=ref.get("idref"), linear=ref.get("linear"))
        for ref in opf_root.findall(".//opf:spine/opf:itemref", _NS)
        if ref.get("idref")
    ]


def _extract_cover(
    zf: zipfile.ZipFile,
    opf_root: ET.Element,
    manifest: dict[str, _ManifestItem],
    cover_dir: str,
) -> str | None:
    """Write the cover image to ``cover_dir`` and return its path, or None."""
    import os

    cover_href: str | None = None

    # EPUB3: <item properties="cover-image">. EPUB2: <meta name="cover" content=id>.
    for item in opf_root.findall(".//opf:manifest/opf:item", _NS):
        if "cover-image" in (item.get("properties") or ""):
            cover_href = _resolve_manifest_href(item.get("id"), manifest)
            break
    if cover_href is None:
        meta = opf_root.find(".//opf:metadata/opf:meta[@name='cover']", _NS)
        if meta is not None:
            manifest_item = manifest.get(meta.get("content", ""))
            cover_href = manifest_item.href if manifest_item else None

    if cover_href is None:
        return None
    try:
        data = zf.read(cover_href)
    except KeyError:
        return None

    os.makedirs(cover_dir, exist_ok=True)
    ext = posixpath.splitext(cover_href)[1] or ".jpg"
    out_path = os.path.join(cover_dir, f"cover{ext}")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def _resolve_manifest_href(item_id: str | None, manifest: dict[str, _ManifestItem]) -> str | None:
    if not item_id:
        return None
    item = manifest.get(item_id)
    return item.href if item else None


def _parse_document(html: str, href: str, index: int) -> ParsedChapter | None:
    """Extract a chapter from one XHTML document, or None if it's front matter.

    ``href`` is the archive path, used to recognize front matter by filename.
    """
    if _SKIP.search(posixpath.basename(href)):
        logger.info("Skipping front-matter document %r (matched title/filename pattern)", href)
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Read the title from the body heading only. The <head><title> is often
    # empty or a placeholder ("Unknown") in scraped EPUBs. Clean before the
    # _SKIP check so invisible Unicode noise (e.g. a soft hyphen splitting
    # "Con\xadtents") can't hide a title we'd otherwise recognize as front matter.
    body = soup.find("body") or soup
    heading = body.find(["h1", "h2", "h3"])
    title = _clean(heading.get_text()) if heading else ""

    paragraphs: list[str] = []
    for p in body.find_all("p"):
        text = _clean(p.get_text())
        if text:
            paragraphs.append(text)

    words = sum(len(p.split()) for p in paragraphs)
    if words < _MIN_CHAPTER_WORDS:
        return None
    if title and _SKIP.search(title):
        logger.info("Skipping front-matter document %r (matched title/filename pattern)", href)
        return None

    return ParsedChapter(
        index=index,
        title=title or f"Chapter {index}",
        paragraphs=paragraphs,
    )


def _clean(text: str) -> str:
    """Collapse whitespace and strip invisible Unicode noise.

    EPUB text is full of newlines, non-breaking spaces, soft hyphens, and
    zero-width characters (often from scraped/converted sources) that would
    otherwise split words apart and let front matter like "Con\xadtents" slip
    past the _SKIP filename/title check.
    """
    text = text.replace("\xa0", " ")
    # Soft hyphen, zero-width space/non-joiner/joiner, BOM: invisible, so drop
    # them entirely rather than replacing with a space.
    for invisible in ("­", "​", "‌", "‍", "﻿"):
        text = text.replace(invisible, "")
    return re.sub(r"\s+", " ", text).strip()


def _join(base: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base, href)) if base else href
