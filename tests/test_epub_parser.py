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
