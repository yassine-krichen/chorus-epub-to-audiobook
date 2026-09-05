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
    assert [c.index for c in selected] == [3, 4, 5]


def test_select_chapters_raises_when_start_chapter_is_beyond_the_books_chapter_count(tmp_path):
    p = _pipeline_with_start_chapter(tmp_path, start_chapter=10)
    with pytest.raises(ValueError, match=r"start_chapter=10 is beyond this book's 5 chapter"):
        p._select_chapters(_book(5))
