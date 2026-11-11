"""Client-customizable download filenames, built without ``str.format``.

``str.format``/``format_map`` on a fully client-controlled template string is
an SSTI-style injection risk (arbitrary attribute/index access via format
spec mini-language). Instead we allowlist the exact placeholder shapes we
support via regex and substitute them ourselves.
"""

from __future__ import annotations

import re

# Allowed placeholders: {title}, {part}, or {part:0Nd} (zero-padded width N).
# The width is at most two ASCII digits (``\d`` would also match other Unicode
# digits) and capped by MAX_PART_WIDTH: ``zfill`` with a client-chosen width
# like 999999999 would allocate ~1 GB per request.
MAX_PART_WIDTH = 10
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_ALLOWED = re.compile(r"^\{title\}$|^\{part\}$|^\{part:0(?P<width>[0-9]{1,2})d\}$")

# Filesystem-unsafe characters across Windows/macOS/Linux, plus control chars.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Unicode control characters (Cc), line/paragraph separators (Zl, Zp) and bidi
# controls: invisible, and able to spoof a filename's displayed extension or
# direction in a Content-Disposition header. Other format characters (ZWNJ,
# ZWJ, soft hyphen) are deliberately kept: Persian and emoji titles need them.
_UNSAFE_UNICODE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


def validate_filename_template(template: str) -> None:
    """Raise ``ValueError`` if ``template`` contains any placeholder other
    than ``{title}``, ``{part}``, or ``{part:0Nd}``, or if its *literal*
    text (everything outside the placeholders) contains characters unsafe
    for a filename / an HTTP ``Content-Disposition`` header value.

    The substituted values (``title``, ``part``) are sanitized separately in
    ``render_filename`` -- this check exists because the static text around
    them is entirely client-controlled and was previously never inspected,
    which let a template like ``a"; foo="bar{title}`` or a template
    containing a raw CRLF break out of the quoted filename parameter used
    downstream (see ``storage.r2._content_disposition``).
    """
    for match in _PLACEHOLDER.finditer(template):
        allowed = _ALLOWED.match(match.group(0))
        if not allowed:
            raise ValueError(
                f"Unsupported filename placeholder {match.group(0)!r}. "
                "Allowed placeholders: {title}, {part}, {part:0Nd}"
            )
        _check_part_width(allowed)
    _reject_unsafe_literal_text(_PLACEHOLDER.sub("", template))


def _check_part_width(allowed: re.Match[str]) -> None:
    width = allowed.group("width")
    if width is not None and int(width) > MAX_PART_WIDTH:
        raise ValueError(
            f"Part width {int(width)} in {allowed.group(0)!r} exceeds the "
            f"maximum of {MAX_PART_WIDTH}"
        )


def _sanitize_title(title: str) -> str:
    cleaned = _UNSAFE_UNICODE.sub("", _UNSAFE_CHARS.sub("_", title))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "book"


def _reject_unsafe_literal_text(literal_text: str) -> None:
    """Raise ``ValueError`` if client-controlled literal text contains
    characters that could break out of a quoted HTTP header parameter
    (``"``, ``\\``), traverse the filesystem (``..``, ``/``), or inject
    header/line content (control characters, including ``\\r``/``\\n``)."""
    if _UNSAFE_CHARS.search(literal_text) or _UNSAFE_UNICODE.search(literal_text):
        raise ValueError(
            "filename_template contains unsafe characters outside its "
            "placeholders (quotes, backslashes, path separators, or "
            "control or bidi characters are not allowed)"
        )
    if ".." in literal_text:
        raise ValueError("filename_template cannot contain '..' outside its placeholders")


def render_filename(template: str, *, title: str, part: int, extension: str = "m4b") -> str:
    """Render ``template`` into a safe filename, appending ``extension`` once."""
    # Guarded by validate_filename_template at job-creation time; a template
    # that reaches here has already had its literal text checked. Checked
    # again here as defense in depth, independent of whether the caller
    # actually validated first (see test_render_filename_raises_for_a_*
    # below for the same pattern applied to disallowed placeholders).
    _reject_unsafe_literal_text(_PLACEHOLDER.sub("", template))
    safe_title = _sanitize_title(title)

    def _substitute(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        allowed = _ALLOWED.match(placeholder)
        if not allowed:
            # Guarded by validate_filename_template at job-creation time; a
            # template that reaches here has already been validated.
            raise ValueError(f"Unsupported filename placeholder {placeholder!r}")
        if placeholder == "{title}":
            return safe_title
        if placeholder == "{part}":
            return str(part)
        _check_part_width(allowed)
        width = int(allowed.group("width"))
        return str(part).zfill(width)

    rendered = _PLACEHOLDER.sub(_substitute, template)
    rendered = rendered.strip()
    suffix = f".{extension}"
    if rendered.lower().endswith(suffix.lower()):
        return rendered
    return f"{rendered}{suffix}"
