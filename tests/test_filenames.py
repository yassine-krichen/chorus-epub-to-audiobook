"""Tests for client-customizable download filenames (book2audio.api.jobs.filenames).

render_filename is built without str.format specifically to avoid SSTI-style
injection via the format spec mini-language (arbitrary attribute/index
access, e.g. {title.__class__}). These tests focus on that injection
resistance, plus the filesystem-safety and extension-handling behavior.
"""

from __future__ import annotations

import pytest

from book2audio.api.jobs.filenames import render_filename, validate_filename_template


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


def test_render_filename_with_an_empty_template_still_appends_the_extension():
    assert render_filename("", title="Book", part=1) == ".m4b"


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
