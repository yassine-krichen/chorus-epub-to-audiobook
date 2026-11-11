"""Tests for client-customizable download filenames (book2audio.assemble.filenames).

render_filename is built without str.format specifically to avoid SSTI-style
injection via the format spec mini-language (arbitrary attribute/index
access, e.g. {title.__class__}). These tests focus on that injection
resistance, plus the filesystem-safety and extension-handling behavior.
"""

from __future__ import annotations

import unicodedata

import pytest

from book2audio.assemble.filenames import (
    PER_CHAPTER_FILENAME_TEMPLATE,
    PartInfo,
    default_filename_template,
    render_filename,
    render_part_filenames,
    validate_chapter_title_placeholder,
    validate_filename_template,
)
from book2audio.storage import r2


@pytest.mark.parametrize(
    "template",
    [
        "{title}",
        "{part}",
        "{part:03d}",
        "{title} - Part {part:02d}",
        "static-name-no-placeholders",
        "",
    ],
)
def test_validate_filename_template_accepts_allowed_placeholders(template):
    validate_filename_template(template)  # must not raise


@pytest.mark.parametrize(
    "template",
    [
        "{title.__class__}",
        "{title.__class__.__mro__}",
        "{part!r}",
        "{part:d}",
        "{part:2d}",
        "{0}",
        "{1}",
        "{title:>20}",
        "{title!s:>20}",
        "{part.__init__}",
        "{author}",
    ],
)
def test_validate_filename_template_rejects_disallowed_placeholders(template):
    with pytest.raises(ValueError, match="Unsupported filename placeholder"):
        validate_filename_template(template)


def test_validate_filename_template_only_flags_the_innermost_brace_pair_when_nested():
    """Documents current behavior rather than asserting an assumption: the
    placeholder regex ({[^{}]*}) can't match across a nested '{', so for
    "{title{part}}" only the inner "{part}" is ever inspected/substituted --
    the stray outer braces are left as literal text by render_filename. This
    is not an injection risk (no attribute/index access is performed on
    them), just a cosmetic edge case worth documenting."""
    validate_filename_template("{title{part}}")  # does not raise

    rendered = render_filename("{title{part}}", title="Book", part=3)

    assert rendered == "{title3}.m4b"


def test_render_filename_substitutes_title_and_part():
    assert render_filename("{title} - Part {part}", title="My Book", part=3) == "My Book - Part 3.m4b"


def test_render_filename_zero_pads_the_part_number():
    assert render_filename("{title}-{part:03d}", title="Book", part=7) == "Book-007.m4b"


def test_render_filename_does_not_double_the_extension_when_already_present():
    assert render_filename("{title}.m4b", title="Book", part=1) == "Book.m4b"


def test_render_filename_extension_check_is_case_insensitive():
    assert render_filename("{title}.M4B", title="Book", part=1) == "Book.M4B"


def test_render_filename_uses_a_custom_extension_when_given():
    assert render_filename("{title}", title="Book", part=1, extension="mp3") == "Book.mp3"


def test_render_filename_sanitizes_filesystem_unsafe_characters_in_the_title():
    result = render_filename("{title}", title='Book: A "Tale"/Story?', part=1)
    assert result == "Book_ A _Tale__Story_.m4b"


def test_render_filename_falls_back_to_book_when_the_sanitized_title_is_empty():
    assert render_filename("{title}", title="...", part=1) == "book.m4b"


def test_render_filename_strips_surrounding_whitespace_from_a_static_template():
    assert render_filename("  static-name  ", title="Book", part=1) == "static-name.m4b"


def test_render_filename_with_an_empty_template_falls_back_to_a_part_name():
    assert render_filename("", title="Book", part=1) == "part-01.m4b"


def test_render_filename_raises_for_a_placeholder_that_was_never_validated():
    """Defense in depth: render_filename itself rejects a disallowed
    placeholder even if validate_filename_template was skipped upstream."""
    with pytest.raises(ValueError, match="Unsupported filename placeholder"):
        render_filename("{title.__class__}", title="Book", part=1)


# --- literal-text injection resistance (Content-Disposition header) -------
#
# validate_filename_template/render_filename previously only sanitized the
# *substituted* title value -- the static/literal text surrounding the
# placeholders was never inspected, so a template's literal text could break
# out of the quoted filename="..." parameter built downstream in
# storage.r2._content_disposition, or smuggle a raw CRLF into the header.


def test_validate_filename_template_rejects_a_quote_breakout_in_literal_text():
    with pytest.raises(ValueError, match="unsafe characters"):
        validate_filename_template('a"; foo="bar{title}')


def test_validate_filename_template_rejects_a_crlf_header_injection_in_literal_text():
    with pytest.raises(ValueError, match="unsafe characters"):
        validate_filename_template("a\r\nSet-Cookie: evil=1{title}")


def test_validate_filename_template_rejects_path_traversal_in_literal_text():
    with pytest.raises(ValueError, match="unsafe characters|'\\.\\.'"):
        validate_filename_template("../../etc/passwd-{title}")


def test_validate_filename_template_rejects_a_bare_dot_dot_in_literal_text():
    with pytest.raises(ValueError, match="\\.\\."):
        validate_filename_template("{title}..evil")


def test_validate_filename_template_accepts_normal_punctuation_in_literal_text():
    validate_filename_template("{title} (Part {part:02d}) - Audiobook, vol. 1!")  # must not raise


def test_render_filename_rejects_a_quote_breakout_in_literal_text_even_if_unvalidated():
    """Defense in depth: render_filename rejects unsafe literal text on its
    own, even if validate_filename_template was skipped upstream."""
    with pytest.raises(ValueError, match="unsafe characters"):
        render_filename('a"; foo="bar{title}', title="Book", part=1)


def test_render_filename_rejects_a_crlf_header_injection_in_literal_text_even_if_unvalidated():
    with pytest.raises(ValueError, match="unsafe characters"):
        render_filename("a\r\nSet-Cookie: evil=1{title}", title="Book", part=1)


def test_render_filename_accepts_normal_punctuation_in_literal_text():
    result = render_filename(
        "{title} (Part {part:02d}) - Audiobook, vol. 1!", title="My Book", part=2
    )
    assert result == "My Book (Part 02) - Audiobook, vol. 1!.m4b"


# --- resource-exhaustion and invisible-character hardening ------------------


@pytest.mark.parametrize("template", ["{part:011d}", "{part:099d}", "{part:0999999999d}"])
def test_part_width_above_cap_is_rejected(template):
    with pytest.raises(ValueError):
        validate_filename_template(template)
    with pytest.raises(ValueError):
        render_filename(template, title="Book", part=1)


def test_part_width_at_cap_is_accepted():
    validate_filename_template("{part:010d}")  # must not raise
    assert render_filename("{part:010d}", title="Book", part=7) == "0000000007.m4b"


def test_filename_template_longer_than_200_chars_is_rejected():
    from pydantic import ValidationError

    from book2audio.api.jobs.schemas import JobConfig

    JobConfig(filename_template="a" * 200)  # boundary must not raise
    with pytest.raises(ValidationError, match="at most 200"):
        JobConfig(filename_template="a" * 201)


def test_bidi_override_in_title_is_stripped_from_filename():
    result = render_filename("{title}", title="Book\u202etxt.exe\u2066", part=1)
    assert result == "Booktxt.exe.m4b"


def test_line_separator_in_title_is_stripped_from_filename():
    assert render_filename("{title}", title="a\u2028b\u2029c", part=1) == "abc.m4b"


def test_zero_width_joiners_in_title_survive_for_persian_and_emoji_titles():
    title = "\u06a9\u062a\u0627\u0628\u200c\u0647\u0627 \U0001f468\u200d\U0001f469"
    assert render_filename("{title}", title=title, part=1) == f"{title}.m4b"


@pytest.mark.parametrize("char", ["\u202e", "\u200f", "\u2066", "\u2028"])
def test_validate_filename_template_rejects_bidi_and_separator_chars_in_literal_text(char):
    with pytest.raises(ValueError, match="unsafe characters"):
        validate_filename_template(f"a{char}b{{title}}")


def test_part_width_with_non_ascii_digits_is_rejected():
    # Arabic-Indic digits: Python's \d matches them, int() accepts them.
    with pytest.raises(ValueError, match="Unsupported filename placeholder"):
        validate_filename_template("{part:0\u0661\u0660d}")


# --- {chapter} and {chapter_title} -------------------------------------------


def _one_chapter_parts(*titles, first_number=1):
    return [PartInfo(i, first_number + i - 1, t) for i, t in enumerate(titles, start=1)]


def _render_chapters(*titles, template=PER_CHAPTER_FILENAME_TEMPLATE):
    return render_part_filenames(template, title="Book", parts=_one_chapter_parts(*titles))


@pytest.mark.parametrize(
    "template", ["{chapter}", "{chapter:03d}", "{chapter_title}", "{chapter:03d} - {chapter_title}"]
)
def test_validate_filename_template_accepts_the_chapter_placeholders(template):
    validate_filename_template(template)  # must not raise


@pytest.mark.parametrize(
    "template", ["{chapter:011d}", "{chapter:3d}", "{chapter_title:>20}", "{chapter_number}"]
)
def test_validate_filename_template_rejects_malformed_chapter_placeholders(template):
    with pytest.raises(ValueError):
        validate_filename_template(template)


def test_chapter_title_prefix_is_removed_so_the_number_is_not_repeated():
    assert _render_chapters("Chapter 1: Nightmare Begins") == ["001 - Nightmare Begins.m4b"]


@pytest.mark.parametrize(
    "raw",
    [
        "Chapter 12 - Nightmare Begins",
        "Ch. 12: Nightmare Begins",
        "Chapter XII. Nightmare Begins",
        "Chapter 12 \u2013 Nightmare Begins",
    ],
)
def test_chapter_title_prefix_variants_are_all_removed(raw):
    names = render_part_filenames("{chapter_title}", title="Book", parts=[PartInfo(1, 12, raw)])
    assert names == ["Nightmare Begins.m4b"]


def test_chapter_title_without_a_prefix_is_kept_as_is():
    assert _render_chapters("Prologue") == ["001 - Prologue.m4b"]


def test_empty_chapter_title_falls_back_to_chapter_and_its_number():
    names = render_part_filenames(
        PER_CHAPTER_FILENAME_TEMPLATE, title="Book", parts=[PartInfo(1, 7, "")]
    )
    assert names == ["007 - Chapter 7.m4b"]


def test_title_that_is_only_a_chapter_prefix_falls_back_to_chapter_and_its_number():
    names = render_part_filenames(
        PER_CHAPTER_FILENAME_TEMPLATE, title="Book", parts=[PartInfo(1, 3, "Chapter 3:")]
    )
    assert names == ["003 - Chapter 3.m4b"]


def test_chapter_title_made_only_of_dots_and_spaces_falls_back():
    assert _render_chapters(" . . ") == ["001 - Chapter 1.m4b"]


def test_chapter_title_replaces_windows_illegal_characters_and_collapses_whitespace():
    result = _render_chapters('What?   A "Test" <of> a|b\\c/d*\n e')[0]
    assert result == "001 - What_ A _Test_ _of_ a_b_c_d_ e.m4b"


def test_chapter_title_trailing_dots_and_spaces_are_stripped():
    assert _render_chapters("The End...  ") == ["001 - The End.m4b"]


def test_chapter_title_longer_than_80_chars_is_cut_on_a_word_boundary():
    title = " ".join(["word"] * 40)  # 199 chars
    result = _render_chapters(title)[0]
    cut = result.removeprefix("001 - ").removesuffix(".m4b")
    assert len(cut) <= 80
    assert set(cut.split()) == {"word"}  # no half word at the end


def test_chapter_title_single_giant_word_is_cut_hard_at_80_chars():
    result = _render_chapters("x" * 500)[0]
    assert result == "001 - " + "x" * 80 + ".m4b"


def test_whole_filename_stem_is_capped_at_150_chars():
    name = render_part_filenames("a" * 190, title="Book", parts=[PartInfo(1, 1, "")])[0]
    assert name == "a" * 150 + ".m4b"


@pytest.mark.parametrize(
    "stem", ["CON", "con", "Prn", "AUX", "NUL", "COM1", "com9", "LPT1", "lpt9"]
)
def test_windows_reserved_stem_is_prefixed_with_an_underscore(stem):
    names = render_part_filenames("{title}", title=stem, parts=[PartInfo(1, 1, "")])
    assert names == [f"_{stem}.m4b"]


def test_reserved_stem_from_a_chapter_title_is_prefixed():
    names = render_part_filenames("{chapter_title}", title="Book", parts=[PartInfo(1, 1, "Nul")])
    assert names == ["_Nul.m4b"]


def test_a_stem_that_only_starts_with_a_reserved_name_is_left_alone():
    names = render_part_filenames("{title}", title="CONTACT", parts=[PartInfo(1, 1, "")])
    assert names == ["CONTACT.m4b"]


def test_duplicate_names_are_made_unique_ignoring_case():
    names = _render_chapters("Dawn", "dawn", "DAWN", template="{chapter_title}")
    assert names == ["Dawn.m4b", "dawn (2).m4b", "DAWN (3).m4b"]


def test_a_deduplicated_name_never_collides_with_a_later_real_name():
    names = _render_chapters("Dawn", "Dawn", "Dawn (2)", template="{chapter_title}")
    assert len({n.lower() for n in names}) == 3


def test_numbered_names_are_unique_without_any_suffix():
    names = _render_chapters("Same", "Same")
    assert names == ["001 - Same.m4b", "002 - Same.m4b"]


def test_chapter_placeholder_uses_the_parts_first_book_chapter_number():
    names = render_part_filenames(
        "{title} ch{chapter:03d}", title="Book", parts=[PartInfo(1, 41, ""), PartInfo(2, 51, "")]
    )
    assert names == ["Book ch041.m4b", "Book ch051.m4b"]


def test_render_filename_wrapper_accepts_chapter_and_chapter_title():
    result = render_filename(
        PER_CHAPTER_FILENAME_TEMPLATE,
        title="Book",
        part=1,
        chapter=5,
        chapter_title="Chapter 5: Hi",
    )
    assert result == "005 - Hi.m4b"


@pytest.mark.parametrize(
    "hostile",
    [
        'a"; filename="evil.exe',
        "line1\r\nSet-Cookie: x=1",
        "rtl\u202etxt.exe",
        "sep\u2028arator\u2029x",
        "back\\slash",
        "\x00nul\x1fctl\x7f",
    ],
)
def test_hostile_chapter_titles_never_break_the_content_disposition_header(hostile):
    """Rendered names go straight into r2's header builder, which rejects unsafe
    characters outright, so a name that reaches it unsanitized would raise."""
    name = _render_chapters(hostile)[0]
    disposition = r2._content_disposition(name)  # must not raise
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2


def test_hostile_book_title_never_breaks_the_content_disposition_header():
    name = render_filename("{title}", title='x"\r\n\u202e', part=1)
    r2._content_disposition(name)  # must not raise


def test_chapter_title_placeholder_is_rejected_unless_one_file_per_chapter():
    validate_chapter_title_placeholder("{chapter_title}", 1)  # must not raise
    for batch_size in (0, 2, 10):
        with pytest.raises(ValueError, match="one file per chapter"):
            validate_chapter_title_placeholder("{chapter_title}", batch_size)


def test_chapter_title_check_ignores_templates_without_the_placeholder():
    validate_chapter_title_placeholder("{title} - {part}", 10)
    validate_chapter_title_placeholder(None, 10)


def test_default_template_is_per_chapter_only_for_one_file_per_chapter():
    assert default_filename_template(1) == PER_CHAPTER_FILENAME_TEMPLATE
    assert default_filename_template(10) is None
    assert default_filename_template(0) is None


# --- byte limits, empty stems, more reserved names, tidy dedupe ---------------


def _fits_filesystems(name):
    """ext4 limits a name to 255 bytes, NTFS to 255 UTF-16 units."""
    return len(name.encode("utf-8")) <= 255 and len(name.encode("utf-16-le")) // 2 <= 255


def test_emoji_title_is_cut_by_bytes_so_the_name_fits_every_filesystem():
    name = render_part_filenames(
        "{title}", title="\U0001f600" * 100, parts=[PartInfo(1, 1, "")]
    )[0]
    assert _fits_filesystems(name)
    assert name.removesuffix(".m4b") == "\U0001f600" * 50  # whole emoji only, 200 bytes


def test_cjk_title_with_a_chapter_title_is_cut_by_bytes_on_a_character_boundary():
    name = render_part_filenames(
        "{title} - {chapter_title}",
        title="\u4e2d\u6587\u5c0f\u8bf4" * 20,
        parts=[PartInfo(1, 1, "\u7b2c\u4e00\u7ae0" * 30)],
    )[0]
    assert _fits_filesystems(name)
    stem = name.removesuffix(".m4b")
    assert len(stem.encode("utf-8")) <= 200
    stem.encode("utf-8").decode("utf-8")  # no half character left behind


def test_cut_does_not_leave_a_dangling_combining_mark():
    # "e" + COMBINING ACUTE ACCENT, 3 bytes per pair; the cut lands after a base letter.
    title = "e\u0301" * 100
    stem = render_part_filenames("{title}", title=title, parts=[PartInfo(1, 1, "")])[0]
    stem = stem.removesuffix(".m4b")
    assert len(stem.encode("utf-8")) <= 200
    assert not unicodedata.combining(stem[-1])


def test_cut_does_not_leave_a_dangling_zero_width_joiner():
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"  # man, woman, girl joined
    stem = render_part_filenames(
        "{title}", title=family * 20, parts=[PartInfo(1, 1, "")]
    )[0].removesuffix(".m4b")
    assert not stem.endswith("\u200d")


def test_duplicate_long_names_still_fit_after_the_copy_suffix():
    title = "\U0001f600" * 100
    names = render_part_filenames(
        "{title}", title=title, parts=[PartInfo(n, n, "") for n in range(1, 13)]
    )
    assert len({name.lower() for name in names}) == 12
    assert names[-1].endswith(" (12).m4b")
    assert all(_fits_filesystems(name) for name in names)


@pytest.mark.parametrize("template", [" ", ".", "  . "])
def test_template_that_renders_to_nothing_falls_back_to_a_part_name(template):
    names = render_part_filenames(
        template, title="Book", parts=[PartInfo(1, 1, ""), PartInfo(2, 2, "")]
    )
    assert names == ["part-01.m4b", "part-02.m4b"]


@pytest.mark.parametrize(
    "stem", ["COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "lpt\u00b2", "LPT\u00b3", "CONIN$", "conout$"]
)
def test_more_windows_reserved_names_are_prefixed(stem):
    names = render_part_filenames("{title}", title=stem, parts=[PartInfo(1, 1, "")])
    assert names == [f"_{stem}.m4b"]


def test_a_name_already_ending_in_a_copy_number_continues_the_count():
    names = _render_chapters("A (2)", "A (2)", "A (2)", template="{chapter_title}")
    assert names == ["A (2).m4b", "A (3).m4b", "A (4).m4b"]


def test_a_copy_of_a_copy_is_never_stacked():
    names = _render_chapters("A", "A", "A (2)", template="{chapter_title}")
    assert names == ["A.m4b", "A (2).m4b", "A (3).m4b"]
    assert all(" (2) (" not in name for name in names)
