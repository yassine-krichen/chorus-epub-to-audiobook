import pytest

from book2audio.config import Config
from book2audio.models import Book, BookMetadata, Chapter
from book2audio.pipeline import Pipeline, _plan_batches


def _chapters(n):
    return [Chapter(index=i, title=f"Chapter {i}") for i in range(1, n + 1)]


def _book(n):
    return Book(metadata=BookMetadata(title="Book", author="Author"), chapters=_chapters(n))


def test_batches_split_evenly_with_1_based_part_numbers():
    batches = _plan_batches(_chapters(6), batch_size=3)
    assert [(p, [c.index for c in chs]) for p, chs in batches] == [
        (1, [1, 2, 3]),
        (2, [4, 5, 6]),
    ]


def test_final_batch_holds_the_remainder():
    batches = _plan_batches(_chapters(7), batch_size=3)
    assert [c.index for _, chs in batches for c in chs] == [1, 2, 3, 4, 5, 6, 7]
    assert [len(chs) for _, chs in batches] == [3, 3, 1]


def test_batch_size_zero_is_a_single_file():
    batches = _plan_batches(_chapters(5), batch_size=0)
    assert len(batches) == 1
    assert [c.index for c in batches[0][1]] == [1, 2, 3, 4, 5]


def test_batch_size_covering_all_is_a_single_file():
    batches = _plan_batches(_chapters(4), batch_size=10)
    assert len(batches) == 1


def _pipeline(tmp_path):
    cfg = Config(
        epub_path="x.epub",
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
    )
    return Pipeline(cfg)


def test_part_output_path_is_zero_padded(tmp_path):
    p = _pipeline(tmp_path)
    out = p._part_output(single=False, part_no=2, first=4, last=6)
    assert out.endswith("book-part-02.m4b")


def test_single_batch_uses_the_plain_output_path(tmp_path):
    p = _pipeline(tmp_path)
    assert p._part_output(single=True, part_no=1, first=1, last=3) == str(
        tmp_path / "book.m4b"
    )


def _pipeline_with_start_chapter(tmp_path, start_chapter):
    cfg = Config(
        epub_path="x.epub",
        output_path=str(tmp_path / "book.m4b"),
        work_dir=str(tmp_path),
        start_chapter=start_chapter,
    )
    return Pipeline(cfg)


def test_select_chapters_selects_from_start_chapter_to_the_books_end(tmp_path):
    p = _pipeline_with_start_chapter(tmp_path, start_chapter=3)
    selected = p._select_chapters(_book(5))
    assert [c.number for c in selected] == [3, 4, 5]


def test_select_chapters_raises_naming_the_available_numbers_when_nothing_matches(tmp_path):
    p = _pipeline_with_start_chapter(tmp_path, start_chapter=10)
    with pytest.raises(ValueError, match=r"start_chapter=10 matches no chapter.*numbered 1 to 5"):
        p._select_chapters(_book(5))


def _book_with_synopsis(n):
    synopsis = Chapter(index=1, title="SYNOPSIS", number=0, front_matter=True)
    chapters = [Chapter(index=i + 1, title=f"Chapter {i}", number=i) for i in range(1, n + 1)]
    return Book(metadata=BookMetadata(title="Book", author="Author"), chapters=[synopsis, *chapters])


def test_select_chapters_skips_the_synopsis_by_default_and_keeps_index_as_identity(tmp_path):
    p = _pipeline_with_start_chapter(tmp_path, start_chapter=None)
    p.config.chapter_limit = 3
    selected = p._select_chapters(_book_with_synopsis(10))
    assert [(c.number, c.index) for c in selected] == [(1, 2), (2, 3), (3, 4)]


def test_select_chapters_includes_the_synopsis_when_start_chapter_is_zero(tmp_path):
    p = _pipeline_with_start_chapter(tmp_path, start_chapter=0)
    selected = p._select_chapters(_book_with_synopsis(3))
    assert [c.title for c in selected] == ["SYNOPSIS", "Chapter 1", "Chapter 2", "Chapter 3"]


def test_part_metadata_labels_chapters_with_book_numbers(tmp_path):
    book = _book_with_synopsis(10)
    batch = book.chapters[1:4]
    meta = Pipeline._part_metadata(book.metadata, single=False, part_no=1, batch=batch)
    assert meta.title.endswith("Part 1 (Ch 1-3)")
