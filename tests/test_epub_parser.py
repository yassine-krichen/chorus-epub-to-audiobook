import zipfile

import pytest

from book2audio.epub import parse_epub

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package version="2.0" unique-identifier="id"
    xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator opf:role="aut">Jane Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="cover"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

_CH1 = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<title></title>
<h1>Chapter 1: The Start</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
<p>"So it begins," he said, and stood.</p>
</body></html>"""

_COVER = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Cover</h1><p>x</p></body></html>"""


def _make_epub(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CH1)
        zf.writestr("OEBPS/Text/cover.xhtml", _COVER)


def test_parses_metadata_and_narratable_chapters(tmp_path):
    epub = tmp_path / "book.epub"
    _make_epub(epub)

    metadata, chapters = parse_epub(str(epub), cover_dir=str(tmp_path / "cover"))

    assert metadata.title == "Test Book"
    assert metadata.author == "Jane Author"
    assert metadata.language == "en"
    # Cover page is front matter and must be skipped; only ch1 remains.
    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1: The Start"
    assert any("So it begins" in p for p in chapters[0].paragraphs)


def test_rejects_a_non_epub_file(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_text("not a zip")
    with pytest.raises(Exception):
        parse_epub(str(bad), cover_dir=str(tmp_path))


_OPF_WITH_HYPHENATED_TOC = """<?xml version="1.0"?>
<package version="2.0" unique-identifier="id"
    xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator opf:role="aut">Jane Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="toc" href="Text/toc.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="toc"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

# The heading carries a soft hyphen (U+00AD) splitting the word, as produced by
# some scrapers/converters: "Con\xadtents" rather than plain "Contents".
_TOC_WITH_SOFT_HYPHEN = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Con­tents</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
</body></html>"""


def test_skips_toc_titled_with_embedded_soft_hyphen(tmp_path):
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF_WITH_HYPHENATED_TOC)
        zf.writestr("OEBPS/Text/toc.xhtml", _TOC_WITH_SOFT_HYPHEN)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CH1)

    _, chapters = parse_epub(str(epub), cover_dir=str(tmp_path / "cover"))

    # The soft hyphen must be stripped before the _SKIP title check runs, so
    # "Con\xadtents" is recognized as "Contents" and excluded as front matter.
    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1: The Start"


_OPF_WITH_NONLINEAR_SPINE_ITEM = """<?xml version="1.0"?>
<package version="2.0" unique-identifier="id"
    xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator opf:role="aut">Jane Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="note" href="Text/note.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="note" linear="no"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

# Perfectly narratable content, but non-linear ("linear=no" spine items, e.g.
# footnote popups) must be excluded regardless of what they contain.
_NONLINEAR_NOTE = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>A Footnote</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
</body></html>"""


def test_skips_spine_item_marked_non_linear(tmp_path):
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF_WITH_NONLINEAR_SPINE_ITEM)
        zf.writestr("OEBPS/Text/note.xhtml", _NONLINEAR_NOTE)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CH1)

    _, chapters = parse_epub(str(epub), cover_dir=str(tmp_path / "cover"))

    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1: The Start"


_OPF_WITH_NAV_IN_SPINE = """<?xml version="1.0"?>
<package version="3.0" unique-identifier="id"
    xmlns="http://www.idpf.org/2007/opf">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator opf:role="aut">Jane Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml"
        properties="nav"/>
    <item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="nav"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

# Also narratable-looking content, but an EPUB3 nav document in the spine must
# be excluded based on its manifest "properties" attribute.
_NAV_DOCUMENT = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Navigation</h1>
<p>A frail young man sat on a rusty bench across from the old police station,
cradling a cup of coffee in his hands and thinking hard about what was to come
next, and whether any single part of it had ever truly mattered at all, in the
end, to anyone who might one day happen to remember his name.</p>
</body></html>"""


def test_skips_spine_item_with_nav_property(tmp_path):
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("OEBPS/content.opf", _OPF_WITH_NAV_IN_SPINE)
        zf.writestr("OEBPS/Text/nav.xhtml", _NAV_DOCUMENT)
        zf.writestr("OEBPS/Text/ch1.xhtml", _CH1)

    _, chapters = parse_epub(str(epub), cover_dir=str(tmp_path / "cover"))

    assert len(chapters) == 1
    assert chapters[0].title == "Chapter 1: The Start"
