"""Build the 80-line speaker spot-check sheet and turn its labels into committed gold.

    python evals/gender/build_spot_check.py sheet    # writes local/speaker-spot-check.csv
    python evals/gender/build_spot_check.py labels   # reads claude_label (owner_label wins)
                                                     # and writes gold/speaker-labels.json

The sheet holds verbatim book text, so it lives in the gitignored ``local/``
folder. It is stratified by the heuristic's confidence tier (named_trailing,
named_leading, pronoun, alternation, fallback), split evenly across the two gold
slices where each tier has enough lines, so the per-tier accuracy is not
dominated by one tier. The sheet shows the line and its context but NOT the
attributor's answer, so the labeller is not anchored by it. Mark the speaker
with the character's name, ``Narrator`` for words that are not spoken by a
character, or ``SKIP`` when the text does not settle it.

``gold/speaker-labels.json`` keeps only line ids (chapter:ordinal:hash8) and
speaker names, so it is safe to commit.
"""

from __future__ import annotations

import collections
import copy
import csv
import json
import os
import random
import sys

import common
from run_eval import SPEAKER_LABELS, line_id

from book2audio.attribution import HeuristicAttributor
from book2audio.models import Chapter, SegmentKind

SHEET_PATH = os.path.join(common.LOCAL_DIR, "speaker-spot-check.csv")
TIERS = ("named_trailing", "named_leading", "pronoun", "alternation", "fallback")
TOTAL_LINES = 80
CONTEXT_SEGMENTS = 3
SEED = 20261111
_OPEN, _CLOSE = "\u201c", "\u201d"


def _render(chapter: Chapter, position: int) -> str:
    segments = chapter.segments
    parts: list[str] = []
    for j in range(max(0, position - CONTEXT_SEGMENTS), min(len(segments), position + CONTEXT_SEGMENTS + 1)):
        seg = segments[j]
        if seg.kind == SegmentKind.SCENE_BREAK:
            parts.append("* * *")
        elif seg.kind == SegmentKind.DIALOGUE:
            quoted = f"{_OPEN}{seg.text}{_CLOSE}"
            parts.append(f">>> {quoted} <<<" if j == position else quoted)
        else:
            parts.append(seg.text)
    return " \u00b6 ".join(parts)


def _candidates() -> dict[str, list[tuple[str, Chapter, int, int, str]]]:
    """Per tier: ``(slice, chapter, position, ordinal, id)`` for every dialogue line."""
    book = common.load_book()
    by_tier: dict[str, list] = collections.defaultdict(list)
    for slice_name, spec in common.SLICES.items():
        chapters = copy.deepcopy(common.chapters_in(book, spec))
        attributor = HeuristicAttributor()
        attributor.attribute(chapters)
        for chapter in chapters:
            ordinal = 0
            for position, seg in enumerate(chapter.segments):
                if seg.kind != SegmentKind.DIALOGUE:
                    continue
                ordinal += 1
                tier = attributor.tiers[(chapter.index, position)]
                by_tier[tier].append(
                    (slice_name, chapter, position, ordinal, line_id(chapter.index, ordinal, seg.text))
                )
    return by_tier


def _stratified(by_tier: dict[str, list]) -> list[tuple]:
    """``(tier, slice, chapter, position, ordinal, id)`` rows, TOTAL_LINES of them."""
    rng = random.Random(SEED)
    per_tier = TOTAL_LINES // len(TIERS)
    per_slice = per_tier // len(common.SLICES)
    chosen: list[tuple] = []
    taken: set[str] = set()

    def take(tier: str, candidates: list[tuple], limit: int) -> None:
        shuffled = [c for c in candidates if c[-1] not in taken]
        rng.shuffle(shuffled)
        for candidate in shuffled[:limit]:
            chosen.append((tier, *candidate))
            taken.add(candidate[-1])

    for tier in TIERS:
        pool = by_tier.get(tier, [])
        for slice_name in common.SLICES:
            take(tier, [c for c in pool if c[0] == slice_name], per_slice)
        already = sum(1 for row in chosen if row[0] == tier)
        take(tier, pool, per_tier - already)

    # A tier with too few lines leaves a gap; fill it from the biggest tiers.
    for tier in sorted(TIERS, key=lambda t: -len(by_tier.get(t, []))):
        take(tier, by_tier.get(tier, []), TOTAL_LINES - len(chosen))
    chosen.sort(key=lambda row: (row[1], row[2].index, row[4]))
    return chosen


def build_sheet() -> None:
    chosen = _stratified(_candidates())
    os.makedirs(common.LOCAL_DIR, exist_ok=True)
    with open(SHEET_PATH, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "slice", "tier", "line", "context", "claude_label", "owner_label", "owner_note"])
        for tier, slice_name, chapter, position, _ordinal, key in chosen:
            writer.writerow(
                [key, slice_name, tier, chapter.segments[position].text, _render(chapter, position), "", "", ""]
            )
    counts = collections.Counter(c[0] for c in chosen)
    print(f"wrote {SHEET_PATH}: {len(chosen)} lines, per tier {dict(counts)}")


def build_labels() -> None:
    labels: dict[str, str] = {}
    skipped = 0
    with open(SHEET_PATH, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            label = (row["owner_label"] or row["claude_label"]).strip()
            if not label:
                raise SystemExit(f"unlabelled row {row['id']}")
            if label.upper() == "SKIP":
                skipped += 1
                continue
            labels[row["id"]] = label
    payload = {
        "note": "Speaker per dialogue line, labelled by reading the text. Ids are chapter:ordinal:hash8.",
        "labels": labels,
    }
    with open(SPEAKER_LABELS, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {SPEAKER_LABELS}: {len(labels)} labels, {skipped} skipped")


def main(argv: list[str] | None = None) -> int:
    command = (argv or sys.argv[1:] or [""])[0]
    if command == "sheet":
        build_sheet()
    elif command == "labels":
        build_labels()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
