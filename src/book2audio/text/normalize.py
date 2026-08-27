"""Normalize text so the TTS engine pronounces it cleanly.

Kokoro reads raw text well, but a few things make it stumble: em-dashes with no
surrounding space, ellipses, leftover scene markers, and unicode punctuation.
This is intentionally conservative — over-normalizing (e.g. expanding every
number) tends to hurt more than it helps.
"""

from __future__ import annotations

import re

_REPLACEMENTS = {
    "—": ", ",  # em dash -> comma pause
    "–": ", ",  # en dash
    "…": "... ",  # ellipsis char -> spoken pause
    "‘": "'",
    "’": "'",
    "“": "",
    "”": "",
    " ": " ",
}

_WHITESPACE = re.compile(r"\s+")


def normalize_for_tts(text: str) -> str:
    """Return a cleaned, single-spaced version of ``text`` ready for synthesis."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Spaced hyphen used as a dash ("word - word") -> comma pause.
    text = re.sub(r"\s+-\s+", ", ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text
