"""Scoring for the speaker-attribution evaluation. Standard library only.

Everything here is deterministic: the bootstrap takes an explicit seed, and a
gold file that no longer matches the book it was labelled against is a hard
error (a silent mismatch would score the wrong lines).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field

NARRATOR_LABEL = "Narrator"
SKIP_LABEL = "SKIP"

_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = "\"'“”‘’.,;:!?()[]"


class GoldMismatchError(ValueError):
    """The gold labels do not match the book as parsed now."""


def line_text_hash(text: str) -> str:
    """First 8 hex chars of the SHA-256 of the whitespace-normalized line."""
    normalized = _WHITESPACE.sub(" ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def line_id(chapter_index: int, ordinal: int, text: str) -> str:
    """Gold line id ``chapter:ordinal:hash8``.

    ``chapter_index`` is the parser's 1-based chapter index and ``ordinal`` the
    1-based position of the line among that chapter's dialogue segments. The
    hash makes a re-parse that shifts lines fail loudly instead of mislabelling.
    """
    return f"{chapter_index}:{ordinal}:{line_text_hash(text)}"


def window_cluster(book: str, gold_id: str, size: int = 25) -> str:
    """Cluster key for lines that share a window: ``book:chapter:block``.

    Neighbouring lines are not independent (one scene, one pair of speakers, one
    prompt), so the bootstrap can resample these blocks instead of single lines.
    The block size mirrors the attributor's 25-line window without depending on it.
    """
    chapter, ordinal, _digest = gold_id.split(":")
    return f"{book}:{chapter}:{(int(ordinal) - 1) // size}"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


# --- names ------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Case-fold, trim punctuation and a leading article: ``"The Hatter."`` -> ``hatter``."""
    cleaned = _WHITESPACE.sub(" ", name).strip().strip(_EDGE_PUNCTUATION).strip().casefold()
    if cleaned.startswith("the "):
        cleaned = cleaned[4:]
    return cleaned


def build_alias_map(aliases: dict[str, list[str]]) -> dict[str, str]:
    """Map every normalized alias (and canonical name) to its normalized canonical name."""
    mapping: dict[str, str] = {}
    for canonical, variants in aliases.items():
        target = normalize_name(canonical)
        mapping[target] = target
        for variant in variants:
            mapping[normalize_name(variant)] = target
    return mapping


def canonicalize(name: str, alias_map: dict[str, str]) -> str:
    normalized = normalize_name(name)
    return alias_map.get(normalized, normalized)


# --- gold ---------------------------------------------------------------------


@dataclass
class GoldLine:
    id: str
    book: str
    speaker: str
    note: str = ""


@dataclass
class Gold:
    split: str
    books: dict[str, dict]  # book slug -> {"epub_sha256": ..., ...}
    aliases: dict[str, list[str]]
    lines: list[GoldLine] = field(default_factory=list)

    @property
    def alias_map(self) -> dict[str, str]:
        return build_alias_map(self.aliases)

    def to_json(self) -> dict:
        return {
            "split": self.split,
            "books": self.books,
            "aliases": self.aliases,
            "lines": [
                {"id": g.id, "book": g.book, "speaker": g.speaker, "note": g.note}
                for g in self.lines
            ],
        }


def load_gold(path: str) -> Gold:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    ids = [line["id"] for line in raw["lines"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise GoldMismatchError(
            f"{path}: {len(duplicates)} duplicate gold line id(s), e.g. {duplicates[0]}"
        )
    return Gold(
        split=raw["split"],
        books=raw["books"],
        aliases=raw.get("aliases", {}),
        lines=[GoldLine(**line) for line in raw["lines"]],
    )


def verify_gold_matches_book(gold_lines: list[GoldLine], current_ids: set[str]) -> None:
    """Raise :class:`GoldMismatchError` if any gold id is absent from the current parse.

    ``current_ids`` holds the ids of every dialogue line of the book as parsed
    now. An id embeds the line's text hash, so a changed line or a shifted
    ordinal both surface here.
    """
    missing = [g.id for g in gold_lines if g.id not in current_ids]
    if missing:
        sample = ", ".join(missing[:5])
        raise GoldMismatchError(
            f"{len(missing)} gold line id(s) not found in the parsed book (hash or position "
            f"changed), e.g. {sample}. Rebuild the sheet or re-pin the EPUB."
        )


def verify_epub_hash(expected: str, actual: str, book: str) -> None:
    if expected != actual:
        raise GoldMismatchError(
            f"EPUB for {book!r} has sha256 {actual[:12]}..., gold was labelled against "
            f"{expected[:12]}...; refusing to score a different edition."
        )


# --- statistics -----------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion; ``(0, 1)`` when there are no trials."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def paired_bootstrap_diff(
    a: list[int],
    b: list[int],
    iterations: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
    clusters: list[str] | None = None,
) -> tuple[float, float, float]:
    """Mean accuracy difference ``a - b`` with a percentile bootstrap CI.

    ``a`` and ``b`` are per-line 0/1 correctness for the *same* lines; resampling
    keeps the pairing, which is what makes a small gain detectable. Without
    ``clusters`` single lines are resampled. With ``clusters`` (one key per line)
    whole clusters are resampled, which widens the interval when neighbouring
    lines are correlated; the line-level interval is then overconfident.
    The generator is seeded so reruns reproduce the interval exactly.
    """
    if len(a) != len(b) or (clusters is not None and len(clusters) != len(a)):
        raise ValueError("paired samples must have the same length")
    n = len(a)
    if n == 0:
        return 0.0, 0.0, 0.0
    diffs = [x - y for x, y in zip(a, b)]
    rng = random.Random(seed)
    if clusters is None:
        means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(iterations))
    else:
        grouped: dict[str, list[int]] = {}
        for key, d in zip(clusters, diffs):
            totals = grouped.setdefault(key, [0, 0])
            totals[0] += d
            totals[1] += 1
        groups = [tuple(v) for _key, v in sorted(grouped.items())]
        means = []
        for _ in range(iterations):
            picked = rng.choices(groups, k=len(groups))
            means.append(sum(g[0] for g in picked) / sum(g[1] for g in picked))
        means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return sum(diffs) / n, lo, hi


# --- scoring ----------------------------------------------------------------------


@dataclass
class Scored:
    """Per-line correctness over the non-SKIP gold lines, in gold order."""

    ids: list[str]
    correct: list[int]
    skipped: int
    clusters: list[str] = field(default_factory=list)  # window_cluster per scored line

    @property
    def n(self) -> int:
        return len(self.ids)

    @property
    def accuracy(self) -> float:
        return sum(self.correct) / self.n if self.n else 0.0

    def interval(self) -> tuple[float, float]:
        return wilson_interval(sum(self.correct), self.n)

    def by_tier(self, tiers: dict[str, str]) -> dict[str, tuple[int, int]]:
        """``tier -> (correct, total)``; lines missing a tier count under ``unknown``."""
        out: dict[str, list[int]] = {}
        for line_id_, ok in zip(self.ids, self.correct):
            bucket = out.setdefault(tiers.get(line_id_, "unknown"), [0, 0])
            bucket[0] += ok
            bucket[1] += 1
        return {tier: (c, t) for tier, (c, t) in out.items()}


def score_predictions(gold: Gold, predictions: dict[str, str]) -> Scored:
    """Score ``id -> speaker`` predictions against gold, excluding SKIP lines.

    A missing prediction counts as wrong. Gold and predicted names are compared
    after alias canonicalization, so ``"The Hatter"`` equals ``"Mad Hatter"``
    when the alias table says so.
    """
    alias_map = gold.alias_map
    ids: list[str] = []
    correct: list[int] = []
    clusters: list[str] = []
    skipped = 0
    for line in gold.lines:
        if line.speaker.strip().casefold() == SKIP_LABEL.casefold():
            skipped += 1
            continue
        predicted = predictions.get(line.id)
        ok = predicted is not None and canonicalize(predicted, alias_map) == canonicalize(
            line.speaker, alias_map
        )
        ids.append(line.id)
        correct.append(int(ok))
        clusters.append(window_cluster(line.book, line.id))
    return Scored(ids=ids, correct=correct, skipped=skipped, clusters=clusters)


def fixed_and_broken(baseline: Scored, other: Scored) -> tuple[int, int]:
    """Lines ``other`` gets right that ``baseline`` got wrong, and the reverse."""
    if baseline.ids != other.ids:
        raise ValueError("scores cover different lines")
    fixed = sum(1 for b, o in zip(baseline.correct, other.correct) if o and not b)
    broken = sum(1 for b, o in zip(baseline.correct, other.correct) if b and not o)
    return fixed, broken
