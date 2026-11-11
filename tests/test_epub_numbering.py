"""Pure tests for book chapter numbering and front-matter detection."""

from __future__ import annotations

import pytest

from book2audio.epub.numbering import BookNumber, assign_book_numbers


def _numbers(titles, words=None):
    words = words if words is not None else [1500] * len(titles)
    return assign_book_numbers(titles, words)


def test_numbered_titles_carry_their_own_numbers():
    result = _numbers(["Chapter 1: Start", "Chapter 2: Middle", "Chapter 3: End"])
    assert result == [BookNumber(1, False), BookNumber(2, False), BookNumber(3, False)]


def test_a_short_synopsis_before_chapter_one_is_number_zero_front_matter():
    result = _numbers(["SYNOPSIS", "Chapter 1: Start", "Chapter 2: Next"], [100, 1800, 1500])
    assert result[0] == BookNumber(0, True)
    assert [r.number for r in result[1:]] == [1, 2]


def test_a_titled_description_page_is_front_matter_even_when_long():
    result = _numbers(["Book Description", "Chapter 1"], [900, 1500])
    assert result[0] == BookNumber(0, True)


def test_a_real_prologue_is_kept_as_number_zero_and_not_front_matter():
    result = _numbers(["Prologue", "Chapter 1", "Chapter 2"], [150, 1500, 1500])
    assert result[0] == BookNumber(0, False)


def test_a_long_untitled_story_section_before_chapter_one_is_kept():
    result = _numbers(["The Night Before", "Chapter 1", "Chapter 2"], [2500, 1500, 1500])
    assert result[0] == BookNumber(0, False)


def test_a_short_section_before_chapter_one_is_front_matter_unless_it_is_a_story_opener():
    result = _numbers(
        ["A Note", "Interlude", "Chapter 1", "Chapter 2", "Chapter 3"], [120, 120, 1500, 1500, 1500]
    )
    assert [r.front_matter for r in result[:3]] == [True, False, False]


def test_unnumbered_sections_after_the_first_numbered_one_inherit_the_previous_number():
    result = _numbers(["Chapter 1", "Chapter 2", "Interlude", "Chapter 3"])
    assert [r.number for r in result] == [1, 2, 2, 3]
    assert not any(r.front_matter for r in result)


def test_roman_numeral_titles_are_numbered():
    result = _numbers(["CHAPTER I. Down the Rabbit-Hole", "CHAPTER IV. A Bill", "CHAPTER XII. End"])
    assert [r.number for r in result] == [1, 4, 12]


@pytest.mark.parametrize(
    ("title", "number"),
    [("Chapter IX", 9), ("Chapter XL", 40), ("Chapter MCMXC", 1990), ("ch. 7 - Seven", 7)],
)
def test_title_forms_are_parsed(title, number):
    assert _numbers([title, "Chapter 100000"])[0].number == number


def test_lowercase_or_invalid_roman_looking_words_are_not_numbers():
    # "MIX" would be a valid numeral, but "Chapter Mix" must not become 1009.
    result = _numbers(["Chapter Mix", "Chapter Did", "Chapter 1", "Chapter 2"])
    assert [r.number for r in result[2:]] == [1, 2]
    assert result[0].number == 0  # unnumbered, before the first numbered chapter


def test_a_book_with_no_numbered_titles_falls_back_to_position_numbers():
    result = _numbers(["The Boy", "The Girl", "The Dog"], [100, 100, 100])
    assert result == [BookNumber(1, False), BookNumber(2, False), BookNumber(3, False)]


def test_fewer_than_half_numbered_titles_falls_back_to_position_numbers():
    result = _numbers(["Chapter 1", "Two", "Three", "Four"])
    assert [r.number for r in result] == [1, 2, 3, 4]


def test_decreasing_numbers_fall_back_to_position_numbers():
    result = _numbers(["Chapter 1", "Chapter 2", "Chapter 1", "Chapter 2"])
    assert [r.number for r in result] == [1, 2, 3, 4]


def test_story_before_a_first_chapter_that_is_not_one_falls_back_to_position_numbers():
    # Penrod-style: an eBook header section, then a selection of later chapters.
    titles = ["The Project Gutenberg eBook of Penrod", "CHAPTER V THE PAGEANT", "CHAPTER XI DOG"]
    result = _numbers(titles, [6000, 7000, 5500])
    assert result == [BookNumber(1, False), BookNumber(2, False), BookNumber(3, False)]


def test_a_volume_that_starts_at_a_high_chapter_number_keeps_its_numbers():
    result = _numbers(["Chapter 501", "Chapter 502", "Chapter 503"])
    assert [r.number for r in result] == [501, 502, 503]


def test_front_matter_before_a_high_first_chapter_number_keeps_the_book_numbers():
    result = _numbers(["SYNOPSIS", "Chapter 501", "Chapter 502"], [100, 1500, 1500])
    assert result == [BookNumber(0, True), BookNumber(501, False), BookNumber(502, False)]


def test_an_empty_book_has_no_numbers():
    assert assign_book_numbers([], []) == []


def test_mismatched_inputs_are_rejected():
    with pytest.raises(ValueError, match="same length"):
        assign_book_numbers(["Chapter 1"], [])


@pytest.mark.parametrize(
    "title",
    ["Introduction", "Foreword", "Preface", "Author's Note", "Translator’s Note"],
)
def test_introductions_and_notes_are_front_matter_even_when_long(title):
    result = _numbers([title, "Chapter 1", "Chapter 2"], [2500, 1500, 1500])
    assert result[0] == BookNumber(0, True)


def test_a_title_with_an_absurdly_long_chapter_number_is_not_numbered_and_does_not_crash():
    crafted = "Chapter " + "9" * 5000
    result = _numbers([crafted, "Chapter 1", "Chapter 2"])
    assert result[0].number == 0  # not parsed, treated as before the first numbered chapter
    assert [r.number for r in result[1:]] == [1, 2]
