"""Book chapter numbers and front-matter detection.

The parser's ``index`` is a position in reading order, so a synopsis page ahead
of "Chapter 1" shifts every chapter by one. People think in the book's own
numbers ("start at chapter 12", "the first 10 chapters"), so this module derives
those from the titles. It is pure (titles and word counts in, numbers out) so
the rules can be tested without building an EPUB.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Protocol, Sequence

# Keyword is case-insensitive, the numeral is not: roman numerals only count when
# written in capitals, otherwise "Chapter Mix" would read as chapter 1009. Digits
# are capped at 6 so a crafted title cannot make int() parse thousands of digits.
_NUMBERED_TITLE = re.compile(r"^\s*(?i:chapter|ch\.?)\s*(?:(\d{1,6})|([IVXLCDM]+))\b")
_ROMAN = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Titles of pages that are about the book rather than part of the story.
_FRONT_MATTER_TITLE = re.compile(
    r"synopsis|summary|blurb|description|information|about the (?:author|book)"
    r"|dedication|acknowledg|copyright|contents"
    r"|introduction|foreword|preface|author['’]?s note|translator['’]?s note",
    re.I,
)
# Story openers that are short but must be narrated.
_STORY_OPENER_TITLE = re.compile(r"prologue|prelude|interlude", re.I)

# An untitled-as-story page shorter than this is treated as front matter.
_FRONT_MATTER_MAX_WORDS = 300

# Below this share of numbered titles, the book does not number its chapters.
_MIN_NUMBERED_SHARE = 0.5


class Numbered(Protocol):
    """Anything that carries a book chapter number (parsed or built chapters)."""

    @property
    def number(self) -> int: ...

    @property
    def front_matter(self) -> bool: ...


class BookNumber(NamedTuple):
    number: int
    front_matter: bool


def assign_book_numbers(titles: Sequence[str], word_counts: Sequence[int]) -> list[BookNumber]:
    """Give every section its book chapter number and front-matter flag.

    - "Chapter N" titles (digits or capital roman numerals) carry number N.
    - Sections before the first numbered one get number 0. They are front matter
      when the title says so (synopsis, introduction, dedication...), whatever
      their length, or when they are short and not a prologue/prelude/interlude;
      a real prologue keeps number 0 and is kept.
    - Unnumbered sections after the first numbered one inherit the previous number.
    - Books that do not number their chapters reliably fall back to position
      numbering (number = 1-based position, nothing is front matter).
    """
    if len(titles) != len(word_counts):
        raise ValueError("titles and word_counts must have the same length")

    parsed = [_parse_title_number(title) for title in titles]
    first_numbered = next((i for i, n in enumerate(parsed) if n is not None), None)
    if first_numbered is None:
        return _position_numbers(len(titles))

    front_matter_flags = [
        _is_front_matter(titles[i], word_counts[i]) for i in range(first_numbered)
    ]
    if _numbering_is_unreliable(parsed, first_numbered, front_matter_flags):
        return _position_numbers(len(titles))

    numbers: list[BookNumber] = []
    current = 0
    for position, number in enumerate(parsed):
        if position < first_numbered:
            numbers.append(BookNumber(0, front_matter_flags[position]))
            continue
        if number is not None:
            current = number
        numbers.append(BookNumber(current, False))
    return numbers


def _parse_title_number(title: str) -> int | None:
    match = _NUMBERED_TITLE.match(title)
    if match is None:
        return None
    digits, roman = match.groups()
    return int(digits) if digits is not None else _roman_to_int(roman)


def _roman_to_int(numeral: str) -> int | None:
    if not _ROMAN.match(numeral):
        return None
    total = 0
    for char, following in zip(numeral, numeral[1:] + " "):
        value = _ROMAN_VALUES[char]
        total += -value if _ROMAN_VALUES.get(following, 0) > value else value
    return total


def _is_front_matter(title: str, words: int) -> bool:
    if _FRONT_MATTER_TITLE.search(title):
        return True
    return words < _FRONT_MATTER_MAX_WORDS and not _STORY_OPENER_TITLE.search(title)


def _numbering_is_unreliable(
    parsed: list[int | None], first_numbered: int, front_matter_flags: list[bool]
) -> bool:
    numbered = [n for n in parsed if n is not None]
    if len(numbered) < len(parsed) * _MIN_NUMBERED_SHARE:
        return True
    if any(later < earlier for earlier, later in zip(numbered, numbered[1:])):
        return True
    # Story before a first chapter that is not Chapter 1 (or 0): the numbered
    # titles are not this book's chapter sequence, e.g. a collection of stories.
    story_before = not all(front_matter_flags)
    return numbered[0] > 1 and story_before


def _position_numbers(count: int) -> list[BookNumber]:
    return [BookNumber(position, False) for position in range(1, count + 1)]
