"""Customizable output filenames, built without ``str.format``.

Used for hosted download names (``api.jobs``) and for local output files
(``pipeline``), so both name a part the same way.

``str.format``/``format_map`` on a fully client-controlled template string is
an SSTI-style injection risk (arbitrary attribute/index access via format
spec mini-language). Instead we allowlist the exact placeholder shapes we
support via regex and substitute them ourselves.

Chapter titles come from untrusted EPUB text, so every substituted value is
sanitized: control/bidi characters removed, filesystem-illegal characters
replaced, length capped. Rendering all parts of a job at once also makes the
names unique (case-insensitively) and safe on Windows (reserved device names).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Sequence

# Allowed placeholders: {title}, {chapter_title}, {part} or {part:0Nd},
# {chapter} or {chapter:0Nd} (zero-padded width N). The width is at most two
# ASCII digits (``\d`` would also match other Unicode digits) and capped by
# MAX_PART_WIDTH: ``zfill`` with a client-chosen width like 999999999 would
# allocate ~1 GB per request.
MAX_PART_WIDTH = 10
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_ALLOWED = re.compile(
    r"^\{(?:(?P<text>title|chapter_title)"
    r"|(?P<number>part|chapter)(?::0(?P<width>[0-9]{1,2})d)?)\}$"
)
_ALLOWED_HELP = "{title}, {part}, {part:0Nd}, {chapter}, {chapter:0Nd}, {chapter_title}"

# Default when every part holds exactly one chapter (batch_size == 1).
PER_CHAPTER_FILENAME_TEMPLATE = "{chapter:03d} - {chapter_title}"

MAX_CHAPTER_TITLE_LENGTH = 80
MAX_STEM_LENGTH = 150
# Filesystems limit names by bytes (ext4: 255 bytes) or UTF-16 units (NTFS: 255),
# so emoji and CJK titles hit the limit long before 150 characters. 200 bytes
# leaves room for " (NN)" and the extension, and a UTF-16 unit never costs more
# than one UTF-8 byte.
MAX_STEM_BYTES = 200

# Windows refuses these as file names whatever the extension ("CON.m4b").
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{n}" for n in "123456789\u00b9\u00b2\u00b3"}
    | {f"LPT{n}" for n in "123456789\u00b9\u00b2\u00b3"}
)

# Filesystem-unsafe characters across Windows/macOS/Linux, plus control chars.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Unicode control characters (Cc), line/paragraph separators (Zl, Zp) and bidi
# controls: invisible, and able to spoof a filename's displayed extension or
# direction in a Content-Disposition header. Other format characters (ZWNJ,
# ZWJ, soft hyphen) are deliberately kept: Persian and emoji titles need them.
_UNSAFE_UNICODE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)

_WHITESPACE_RUN = re.compile(r"[ \t\r\n\f\v\u00a0]+")

# "Chapter 1:", "Ch. 12 -", "Chapter IV." at the start of a title. The file name
# already carries the number, so repeating it reads "001 - Chapter 1: ...".
_CHAPTER_PREFIX = re.compile(
    r"^\s*(?i:chapter|ch\.?)\s*(?:\d{1,6}|[IVXLCDM]+)\b\s*[:.\-\u2013\u2014]*\s*"
)


@dataclass(frozen=True)
class PartInfo:
    """What a template can say about one output part (file)."""

    part_number: int
    # The book's own number of the part's first chapter.
    chapter_number: int
    # Title of the part's first chapter; only meaningful for one-chapter parts.
    chapter_title: str = ""


def validate_filename_template(template: str) -> None:
    """Raise ``ValueError`` if ``template`` contains any placeholder other
    than the allowed ones, or if its *literal* text (everything outside the
    placeholders) contains characters unsafe for a filename / an HTTP
    ``Content-Disposition`` header value.

    The substituted values are sanitized separately when rendering -- this
    check exists because the static text around them is entirely
    client-controlled and was previously never inspected, which let a template
    like ``a"; foo="bar{title}`` or a template containing a raw CRLF break out
    of the quoted filename parameter used downstream (see
    ``storage.r2._content_disposition``).
    """
    for match in _PLACEHOLDER.finditer(template):
        allowed = _ALLOWED.match(match.group(0))
        if not allowed:
            raise ValueError(
                f"Unsupported filename placeholder {match.group(0)!r}. "
                f"Allowed placeholders: {_ALLOWED_HELP}"
            )
        _check_width(allowed)
    _reject_unsafe_literal_text(_PLACEHOLDER.sub("", template))


def template_uses_chapter_title(template: str) -> bool:
    return "{chapter_title}" in template


def validate_chapter_title_placeholder(template: str | None, batch_size: int) -> None:
    """``{chapter_title}`` names a part after one chapter, so it is only
    meaningful when every part holds exactly one chapter (``batch_size == 1``).
    Otherwise a part would carry the title of its first chapter only, which
    misleads."""
    if template and template_uses_chapter_title(template) and batch_size != 1:
        raise ValueError(
            "The {chapter_title} filename placeholder needs one file per chapter "
            f"(batch_size 1), but batch_size is {batch_size}"
        )


def default_filename_template(batch_size: int) -> str | None:
    """Template used when the user gave none: per-chapter names for one file
    per chapter, nothing (the caller's own naming) otherwise."""
    return PER_CHAPTER_FILENAME_TEMPLATE if batch_size == 1 else None


def _check_width(allowed: re.Match[str]) -> None:
    width = allowed.group("width")
    if width is not None and int(width) > MAX_PART_WIDTH:
        raise ValueError(
            f"Width {int(width)} in {allowed.group(0)!r} exceeds the "
            f"maximum of {MAX_PART_WIDTH}"
        )


def _sanitize(text: str) -> str:
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned)
    cleaned = _UNSAFE_UNICODE.sub("", _UNSAFE_CHARS.sub("_", cleaned))
    return cleaned.strip().strip(".").strip()


def _sanitize_title(title: str) -> str:
    return _sanitize(title) or "book"


def _clean_chapter_title(raw_title: str, chapter_number: int) -> str:
    title = _sanitize(_CHAPTER_PREFIX.sub("", raw_title, count=1))
    if len(title) > MAX_CHAPTER_TITLE_LENGTH:
        head = title[:MAX_CHAPTER_TITLE_LENGTH]
        if title[MAX_CHAPTER_TITLE_LENGTH] != " ":
            # Cut at the last whole word; a single huge word is cut hard.
            head = head.rpartition(" ")[0] or head
        title = head.rstrip(" .")
    return title or f"Chapter {chapter_number}"


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


def render_part_filenames(
    template: str,
    *,
    title: str,
    parts: Sequence[PartInfo],
    extension: str = "m4b",
) -> list[str]:
    """Render ``template`` for every part at once, in order.

    Doing all parts together lets names stay unique: two parts that render to
    the same name (ignoring case, as Windows and macOS do) get " (2)", " (3)"
    appended. Each name ends in ``extension`` exactly once.
    """
    # Guarded by validate_filename_template at job-creation time; a template
    # that reaches here has already had its literal text checked. Checked
    # again here as defense in depth, independent of whether the caller
    # actually validated first.
    _reject_unsafe_literal_text(_PLACEHOLDER.sub("", template))
    safe_title = _sanitize_title(title)

    names: list[str] = []
    taken: set[str] = set()
    for part in parts:
        stem, suffix = _split_extension(
            _render_template(template, safe_title, part).strip(), extension
        )
        # A template that renders to nothing (" " or ".") would leave a bare
        # ".m4b"; the storage-style name keeps it a real, unique file.
        stem = _safe_stem(stem) or f"part-{part.part_number:02d}"
        name = next_free_name(stem, suffix, lambda candidate: candidate.lower() in taken)
        taken.add(name.lower())
        names.append(name)
    return names


def render_filename(
    template: str,
    *,
    title: str,
    part: int,
    extension: str = "m4b",
    chapter: int | None = None,
    chapter_title: str = "",
) -> str:
    """Render one part's filename. ``chapter`` defaults to ``part``."""
    info = PartInfo(part, part if chapter is None else chapter, chapter_title)
    return render_part_filenames(template, title=title, parts=[info], extension=extension)[0]


def _render_template(template: str, safe_title: str, part: PartInfo) -> str:
    def _substitute(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        allowed = _ALLOWED.match(placeholder)
        if not allowed:
            # Guarded by validate_filename_template at job-creation time; a
            # template that reaches here has already been validated.
            raise ValueError(f"Unsupported filename placeholder {placeholder!r}")
        if allowed.group("text") == "title":
            return safe_title
        if allowed.group("text") == "chapter_title":
            return _clean_chapter_title(part.chapter_title, part.chapter_number)
        _check_width(allowed)
        number = part.part_number if allowed.group("number") == "part" else part.chapter_number
        width = allowed.group("width")
        return str(number).zfill(int(width)) if width is not None else str(number)

    return _PLACEHOLDER.sub(_substitute, template)


def _split_extension(rendered: str, extension: str) -> tuple[str, str]:
    suffix = f".{extension}"
    if rendered.lower().endswith(suffix.lower()):
        cut = len(rendered) - len(suffix)
        return rendered[:cut], rendered[cut:]
    return rendered, suffix


def _safe_stem(stem: str) -> str:
    stem = stem[:MAX_STEM_LENGTH]
    stem = _cut_to_bytes(stem, MAX_STEM_BYTES)
    # Windows silently drops trailing dots and spaces, which would make two
    # different names collide on disk.
    stem = stem.rstrip(" .")
    if stem.split(".", 1)[0].rstrip().upper() in _WINDOWS_RESERVED_STEMS:
        return f"_{stem}"
    return stem


def _cut_to_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    # "ignore" drops a code point cut in half. Marks and joiners left dangling
    # at the cut would attach to nothing, so they go too (no extra dependency
    # needed for a grapheme-aware cut).
    cut = encoded[:limit].decode("utf-8", errors="ignore")
    while cut and (unicodedata.combining(cut[-1]) or unicodedata.category(cut[-1]) in _MARKS):
        cut = cut[:-1]
    return cut.rstrip("\u200d\u200c")


_MARKS = frozenset({"Mn", "Mc", "Me"})
_COPY_SUFFIX = re.compile(r"^(?P<base>.*) \((?P<n>[0-9]{1,6})\)$")


def next_free_name(stem: str, suffix: str, is_taken: Callable[[str], bool]) -> str:
    """``stem + suffix``, or the first " (N)" variant that ``is_taken`` accepts.

    A stem that already ends in " (N)" continues from N + 1 instead of stacking
    ("A (2)" then "A (3)", never "A (2) (2)").
    """
    name = f"{stem}{suffix}"
    if not is_taken(name):
        return name
    existing = _COPY_SUFFIX.match(stem)
    base = existing.group("base") if existing else stem
    copy = int(existing.group("n")) + 1 if existing else 2
    while is_taken(name := f"{base} ({copy}){suffix}"):
        copy += 1
    return name
