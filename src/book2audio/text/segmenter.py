"""Split paragraphs into narration and dialogue segments.

A paragraph like::

    Sunny sighed. "So bitter!" he muttered, and drank again.

becomes three ordered segments: narration ("Sunny sighed."), dialogue
("So bitter!"), narration ("he muttered, and drank again."). Keeping the order
lets the assembler alternate voices the way a human reader's ear expects.
"""

from __future__ import annotations

import re

from book2audio.models import Segment, SegmentKind

# Straight and curly double-quote pairs. Novels mix them, so match either the
# straight pair or the curly open/close pair.
_QUOTE = re.compile(r'"([^"]*)"|“([^”]*)”')

# A paragraph that is nothing but ``*** `` / ``* * *`` etc. is a scene break.
_SCENE_BREAK = re.compile(r"^[\s*·•∗＊]{3,}$")


def segment_paragraphs(paragraphs: list[str]) -> list[Segment]:
    """Convert raw chapter paragraphs into ordered, un-attributed segments."""
    segments: list[Segment] = []
    for para in paragraphs:
        if _SCENE_BREAK.match(para.strip()):
            segments.append(Segment(kind=SegmentKind.SCENE_BREAK, text=""))
            continue
        segments.extend(_split_paragraph(para))
    return segments


def _split_paragraph(para: str) -> list[Segment]:
    """Split one paragraph into interleaved narration / dialogue runs."""
    out: list[Segment] = []
    cursor = 0
    for match in _QUOTE.finditer(para):
        before = para[cursor : match.start()].strip()
        if before:
            out.append(Segment(kind=SegmentKind.NARRATION, text=before))

        quoted = (match.group(1) if match.group(1) is not None else match.group(2)).strip()
        if quoted:
            # Speaker resolved later; keep the quotes off the spoken text.
            out.append(Segment(kind=SegmentKind.DIALOGUE, text=quoted))
        cursor = match.end()

    tail = para[cursor:].strip()
    if tail:
        out.append(Segment(kind=SegmentKind.NARRATION, text=tail))

    # A paragraph with no quotes at all is pure narration.
    if not out:
        out.append(Segment(kind=SegmentKind.NARRATION, text=para.strip()))
    return out
