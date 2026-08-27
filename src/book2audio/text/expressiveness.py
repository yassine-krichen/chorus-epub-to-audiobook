"""Cheap, deterministic prosody cues from punctuation.

Kokoro takes plain text with no SSML — there is no way to mark a specific word
for emphasis. What we *can* do is read the shape of a line the way a narrator
glances at punctuation before speaking it: a line ending in "!" gets read a
touch faster and louder, one trailing off in "…" gets read slower and softer.
It is a coarse substitute for real prosody control, not a replacement for it.
"""

from __future__ import annotations

import re

_EXCLAIM = re.compile(r"!+\s*$")
_TRAILING_OFF = re.compile(r"(\.\.\.|…)\s*$")
_QUESTION = re.compile(r"\?\s*$")


def speech_params(text: str) -> tuple[float, float]:
    """Return ``(speed_multiplier, gain_multiplier)`` for ``text``.

    Multipliers are close to 1.0 by design — this nudges delivery, it doesn't
    replace it. Callers apply ``speed_multiplier`` to the synthesis speed and
    ``gain_multiplier`` to the (already loudness-normalized) output samples.
    """
    if _EXCLAIM.search(text):
        return 1.06, 1.15
    if _TRAILING_OFF.search(text):
        return 0.90, 0.85
    if _QUESTION.search(text):
        return 0.97, 1.0
    return 1.0, 1.0
