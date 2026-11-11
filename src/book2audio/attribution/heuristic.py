"""Deterministic, offline speaker attribution.

Strategy, in priority order, for each dialogue segment:

1. A dialogue *tag* in the narration touching the quote — "he said", "asked
   Sunny", "Nephis whispered". Names win outright.
2. If the tag only has a pronoun ("he said"), resolve it to the most recent
   distinct named speaker (crude but right far more often than not in prose).
3. No tag at all: assume a two-person back-and-forth and alternate between the
   last two distinct speakers.

This is not perfect — it is a strong, cheap baseline that makes dialogue-heavy
chapters clearly follow-able. An LLM attributor can replace it later.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from book2audio.attribution.gender import (
    GenderVerdict,
    resolve_genders,
    resolved_genders,
)
from book2audio.models import NARRATOR, Chapter, Segment, SegmentKind

# Verbs that introduce speech. Kept broad; false positives are cheap here.
_SPEECH_VERBS = (
    "said|asked|replied|answered|shouted|muttered|whispered|cried|added|sighed|"
    "murmured|exclaimed|growled|yelled|continued|snapped|called|demanded|"
    "breathed|laughed|hissed|spoke|declared|announced|repeated|responded|"
    "gasped|screamed|roared|grumbled|chuckled|noted|remarked|stated|began|"
    "interrupted|offered|warned|agreed|insisted|admitted|observed|scoffed"
)

# Abbreviated titles end in a period. Without handling them the name pattern
# stops at that period and "Mrs. Schofield" is captured as just "Mrs".
_TITLE = r"(?:(?:Mr|Mrs|Ms|Dr|St|Prof|Capt|Col|Gen|Rev|Messrs)\.\s+)?"

# A proper name: an optional abbreviated title, then one or two capitalized
# words. Avoids all-caps shouting.
_NAME = _TITLE + r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"

# "said Sunny" / "asked the Changing Star" (verb precedes name)
_VERB_THEN_NAME = re.compile(rf"\b(?:{_SPEECH_VERBS})\b\s+(?:the\s+)?({_NAME})")
# "Sunny said" / "Nephis whispered" (name precedes verb)
_NAME_THEN_VERB = re.compile(rf"\b({_NAME})\s+(?:\w+\s+)?(?:{_SPEECH_VERBS})\b")
# "called" also means "named". Passive ("was called Rain") and descriptive ("a
# girl called Rain") naming introduce a character, not a speaker, so these
# phrases are blanked out before any tag is matched. Real speech keeps its
# shape: '"Hey," Sunny called.', 'Sunny called out', '"Come!" called the guard'.
_NAMING_PHRASE = re.compile(
    r"\b(?:(?:was|is|were|are|am|be|been|being)\s+(?:\w+\s+)?"
    r"|(?:a|an|the|his|her|their|my|your|our|its)\s+(?:[A-Za-z'-]+,?\s+){0,3})"
    r"called\b"
)
# A pronoun tag with no name: "he said", "she asked".
_PRONOUN_TAG = re.compile(rf"\b(he|she|they)\b\s+(?:\w+\s+)?(?:{_SPEECH_VERBS})\b", re.I)

# Capitalized words that are not character names, to cut false hits. Covers
# articles/pronouns, sentence-leading conjunctions ("As Cassie said..."), and
# indefinite subjects ("Someone said"). In-world epithets (e.g. "Changing
# Star") are intentionally NOT here — they can be genuine speakers.
_STOPWORDS = {
    "The", "A", "An", "He", "She", "They", "It", "I", "We", "You", "His", "Her",
    "Their", "Then", "But", "And", "So", "Yes", "No", "Sir", "Lord", "Lady",
    "Master", "Mister", "Chapter", "Nightmare", "Spell", "Code", "Black",
    "As", "When", "While", "After", "Before", "Though", "Although", "Because",
    "Since", "If", "Once", "Now", "Here", "There", "Someone", "Everyone",
    "Somebody", "Anyone", "Nobody", "People", "Something", "Nothing", "Both",
    "One", "This", "That", "These", "Those", "What", "Who", "Why", "How",
}


_VOWELS = frozenset("aeiouy")


def _speaker_stats(chapters: list[Chapter]) -> tuple[dict[str, int], dict[str, set[int]]]:
    """Dialogue lines per speaker, and the chapters (by index) each speaker talks in."""
    counts: dict[str, int] = {}
    chapters_of: dict[str, set[int]] = {}
    for chapter in chapters:
        for seg in chapter.segments:
            if seg.kind == SegmentKind.DIALOGUE and seg.speaker:
                counts[seg.speaker] = counts.get(seg.speaker, 0) + 1
                chapters_of.setdefault(seg.speaker, set()).add(chapter.index)
    return counts, chapters_of


def _differ_by_one_vowel(a: str, b: str) -> bool:
    """True for names like ``Cassia``/``Cassie``: same length, one vowel swapped.

    A final ``a``/``o`` swap is never a spelling variant: Maria/Mario,
    Julia/Julio and Antonia/Antonio are a woman and a man.
    """
    if len(a) != len(b) or len(a) < 5 or " " in a or " " in b:
        return False
    diffs = [
        (i, x, y) for i, (x, y) in enumerate(zip(a.lower(), b.lower())) if x != y
    ]
    if len(diffs) != 1:
        return False
    index, x, y = diffs[0]
    if x not in _VOWELS or y not in _VOWELS:
        return False
    return not (index == len(a) - 1 and {x, y} == {"a", "o"})


def _resolve_targets(canonical: dict[str, str]) -> dict[str, str]:
    """Point every folded name at its final target, so no label is left orphaned."""
    resolved: dict[str, str] = {}
    for name, target in canonical.items():
        seen = {name}
        while target in canonical and target not in seen:
            seen.add(target)
            target = canonical[target]
        resolved[name] = target
    return resolved


class HeuristicAttributor:
    """See module docstring. Stateless between calls; state lives per-book.

    After :meth:`attribute`, :attr:`verdicts` holds a
    :class:`~book2audio.attribution.gender.GenderVerdict` per character and
    :attr:`genders` the ``name -> 'm' | 'f'`` map of the confident ones (the
    rest are unresolved and get the narrator's voice).
    """

    def __init__(self, gender_overrides: Mapping[str, str] | None = None) -> None:
        self._gender_overrides = dict(gender_overrides or {})
        self.genders: dict[str, str] = {}
        self.verdicts: dict[str, GenderVerdict] = {}
        # Spelling variants folded into a character ("Cassia" -> "Cassie"), kept so
        # the gender resolver can still read narration that uses the variant.
        self.aliases: dict[str, str] = {}
        # How each dialogue line was resolved, keyed by ``(chapter.index,
        # position in chapter.segments)``. Kept beside the segments (not on
        # Segment) so the shared model stays free of attribution internals.
        self.tiers: dict[tuple[int, int], str] = {}

    def attribute(self, chapters: list[Chapter]) -> list[str]:
        self.tiers = {}
        self.aliases = {}
        discovered: dict[str, None] = {}  # insertion-ordered set of names
        for chapter in chapters:
            self._attribute_chapter(chapter, discovered)
        names = self.merge_aliases(list(discovered), chapters)
        self.infer_genders(names, chapters)
        return names

    def infer_genders(self, names: list[str], chapters: list[Chapter]) -> dict[str, str]:
        """Resolve each character's gender from the whole text (see ``gender.py``).

        Public so another attributor can recompute genders after it changes
        speakers. Returns only the confident ``name -> 'm' | 'f'`` entries and
        keeps every verdict, with its evidence, in :attr:`verdicts`.
        """
        self.verdicts = resolve_genders(
            names, chapters, overrides=self._gender_overrides, aliases=self.aliases
        )
        self.genders = resolved_genders(self.verdicts)
        return self.genders

    def merge_aliases(self, names: list[str], chapters: list[Chapter]) -> list[str]:
        """Fold obvious nicknames and spelling variants into one name.

        Two rules, both cheap and safe; unrelated nicknames are left for an LLM
        attributor. A short single-word name that is a case-insensitive prefix
        of a longer known name is the same character (``Neph`` -> ``Nephis``).
        Two names of the same length (5+) that differ by one vowel are one
        character spelled two ways (``Cassia`` / ``Cassie``: a book's narration
        and dialogue tags can use both), but only when they never speak in the
        same chapter, because two real characters do share scenes. A final
        ``a``/``o`` swap (Maria/Mario) is never merged. The one speaking more
        lines wins. Every fold is recorded in :attr:`aliases` and points at its
        final target.

        Known limit: two different characters with names like this who never
        speak in the same chapter (a Julie in chapter 3 and a Julia in chapter
        40) are still merged.
        """
        canonical: dict[str, str] = {}
        for short in names:
            if " " in short or len(short) < 3:
                continue
            for full in names:
                if full != short and " " not in full and len(full) > len(short):
                    if full.lower().startswith(short.lower()):
                        canonical[short] = full
                        break

        lines, chapters_of = _speaker_stats(chapters)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                if first in canonical or second in canonical:
                    continue
                if not _differ_by_one_vowel(first, second):
                    continue
                if chapters_of.get(first, set()) & chapters_of.get(second, set()):
                    continue
                winner, loser = (
                    (first, second) if lines.get(first, 0) >= lines.get(second, 0)
                    else (second, first)
                )
                # A name that already absorbed another cannot itself be folded away.
                if loser in canonical.values():
                    winner, loser = loser, winner
                    if loser in canonical.values():
                        continue
                canonical[loser] = winner

        canonical = _resolve_targets(canonical)
        if not canonical:
            return names
        self.aliases.update(canonical)
        for chapter in chapters:
            for seg in chapter.segments:
                if seg.speaker in canonical:
                    seg.speaker = canonical[seg.speaker]
        return [n for n in names if n not in canonical]

    def _attribute_chapter(self, chapter: Chapter, discovered: dict[str, None]) -> None:
        segments = chapter.segments
        # recent[-1] is the last speaker, recent[-2] the one before — enough to
        # alternate an untagged two-person exchange.
        recent: list[str] = []
        # Narration indices already used as a line's *trailing* tag ("...", said
        # X). Such a tag binds to the quote before it and must not also be read
        # as a lead-in for the next quote.
        consumed: set[int] = set()

        for i, seg in enumerate(segments):
            if seg.kind == SegmentKind.NARRATION:
                seg.speaker = NARRATOR
                continue
            if seg.kind == SegmentKind.SCENE_BREAK:
                recent.clear()  # a scene break resets conversational context
                continue

            speaker, tag_index, tier = self._resolve_dialogue(segments, i, recent, consumed)
            self.tiers[(chapter.index, i)] = tier
            if tag_index is not None:
                consumed.add(tag_index)
            seg.speaker = speaker
            if speaker != NARRATOR:
                discovered.setdefault(speaker, None)
                if not recent or recent[-1] != speaker:
                    recent.append(speaker)

    def _resolve_dialogue(
        self, segments: list[Segment], i: int, recent: list[str], consumed: set[int]
    ) -> tuple[str, int | None, str]:
        """Return ``(speaker, trailing_tag_index_to_consume_or_None, tier)``.

        ``tier`` says how the speaker was found: ``named_trailing``,
        ``named_leading``, ``pronoun``, ``alternation`` or ``fallback``. The
        first two are explicit names in the text; the rest are guesses.
        """
        after_idx, before_idx = i + 1, i - 1
        after = self._neighbor_narration(segments, after_idx)
        before = "" if before_idx in consumed else self._neighbor_narration(segments, before_idx)

        # 1. Trailing named tag wins ("..." said Sunny); consume it.
        name = self._extract_name(after)
        if name:
            return name, after_idx, "named_trailing"
        # 2. Leading named tag (Sunny said, "...") — not a previous line's tag.
        name = self._extract_name(before)
        if name:
            return name, None, "named_leading"

        # 3. Pronoun-only tag -> most recent distinct named speaker.
        if (_PRONOUN_TAG.search(after) or _PRONOUN_TAG.search(before)) and recent:
            return recent[-1], None, "pronoun"

        # 4. Untagged: alternate between the last two speakers if we have them.
        if len(recent) >= 2:
            return recent[-2], None, "alternation"
        if recent:
            return recent[-1], None, "fallback"
        return NARRATOR, None, "fallback"  # unknown speaker falls back to the narrator voice

    @staticmethod
    def _neighbor_narration(segments: list[Segment], j: int) -> str:
        if 0 <= j < len(segments) and segments[j].kind == SegmentKind.NARRATION:
            return _NAMING_PHRASE.sub(" ", segments[j].text)
        return ""

    @staticmethod
    def _extract_name(text: str) -> str | None:
        if not text:
            return None
        for pattern in (_VERB_THEN_NAME, _NAME_THEN_VERB):
            m = pattern.search(text)
            if m:
                name = m.group(1).strip()
                first = name.split()[0]
                if first not in _STOPWORDS:
                    return name
        return None
