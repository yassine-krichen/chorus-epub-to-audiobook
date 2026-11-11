"""Chapter numbering against the real Shadow Slave 001-100 EPUB (first book only).

Skipped when the EPUB is not on disk. Only the first dozen sections are
asserted on, and the Alice/Penrod fixtures the attribution evals use are checked
to keep exactly the chapters they select today.
"""

from __future__ import annotations

import os

import pytest

from book2audio.config import Config
from book2audio.epub import parse_epub

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHADOW_SLAVE = os.path.join(
    _REPO, "epubs", "Guiltythree - Shadow Slave 001-100 (2022) - libgen.li.epub"
)
_EVAL_DATA = os.path.join(_REPO, "evals", "attribution", "data")


def _config(tmp_path, **overrides) -> Config:
    return Config(
        epub_path="unused.epub",
        output_path=str(tmp_path / "out.m4b"),
        work_dir=str(tmp_path),
        **overrides,
    )


def _parse(path, tmp_path):
    return parse_epub(path, cover_dir=str(tmp_path / "cover"))[1]


@pytest.mark.skipif(not os.path.isfile(_SHADOW_SLAVE), reason="Shadow Slave EPUB not on disk")
class TestShadowSlaveBookOne:
    def test_the_synopsis_is_number_zero_front_matter_and_chapter_one_follows(self, tmp_path):
        sections = _parse(_SHADOW_SLAVE, tmp_path)[:12]

        assert sections[0].title == "SYNOPSIS"
        assert (sections[0].number, sections[0].front_matter) == (0, True)
        assert sections[1].title.startswith("Chapter 1:")
        assert [s.number for s in sections[1:]] == list(range(1, 12))
        # The parser position stays the identity key: chapter 1 is still index 2.
        assert [s.index for s in sections[:3]] == [1, 2, 3]

    def test_the_default_start_with_a_limit_of_ten_selects_chapters_one_to_ten(self, tmp_path):
        sections = _parse(_SHADOW_SLAVE, tmp_path)

        start, end = _config(tmp_path, chapter_limit=10).selection_bounds(sections)
        selected = sections[start:end]

        assert [s.number for s in selected] == list(range(1, 11))
        assert selected[0].title.startswith("Chapter 1:")

    def test_start_chapter_zero_includes_the_synopsis(self, tmp_path):
        sections = _parse(_SHADOW_SLAVE, tmp_path)

        start, _ = _config(tmp_path, start_chapter=0, chapter_limit=10).selection_bounds(sections)

        assert sections[start].title == "SYNOPSIS"


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_EVAL_DATA, "alice-pg11.epub")), reason="Alice fixture missing"
)
def test_alice_keeps_its_twelve_chapters_selected_by_default_with_book_numbers(tmp_path):
    sections = _parse(os.path.join(_EVAL_DATA, "alice-pg11.epub"), tmp_path)

    start, end = _config(tmp_path).selection_bounds(sections)

    assert (start, end) == (0, 12)
    assert [s.number for s in sections] == list(range(1, 13))


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_EVAL_DATA, "penrod-pg402.epub")),
    reason="Penrod fixture missing",
)
def test_penrod_uses_position_numbers_and_selects_everything_as_before(tmp_path):
    sections = _parse(os.path.join(_EVAL_DATA, "penrod-pg402.epub"), tmp_path)

    start, end = _config(tmp_path).selection_bounds(sections)

    assert (start, end) == (0, len(sections))
    assert [s.number for s in sections] == [s.index for s in sections]
    assert not any(s.front_matter for s in sections)
