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

from book2audio.models import NARRATOR, Chapter, Segment, SegmentKind

# Verbs that introduce speech. Kept broad; false positives are cheap here.
_SPEECH_VERBS = (
    "said|asked|replied|answered|shouted|muttered|whispered|cried|added|sighed|"
    "murmured|exclaimed|growled|yelled|continued|snapped|called|demanded|"
    "breathed|laughed|hissed|spoke|declared|announced|repeated|responded|"
    "gasped|screamed|roared|grumbled|chuckled|noted|remarked|stated|began|"
    "interrupted|offered|warned|agreed|insisted|admitted|observed|scoffed"
)

# A proper name: one or two capitalized words. Avoids all-caps shouting.
_NAME = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"

# "said Sunny" / "asked the Changing Star" (verb precedes name)
_VERB_THEN_NAME = re.compile(rf"\b(?:{_SPEECH_VERBS})\b\s+(?:the\s+)?({_NAME})")
# "Sunny said" / "Nephis whispered" (name precedes verb)
_NAME_THEN_VERB = re.compile(rf"\b({_NAME})\s+(?:\w+\s+)?(?:{_SPEECH_VERBS})\b")
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


# Gendered pronouns used to guess a character's voice gender from dialogue tags.
_MALE_PRONOUNS = re.compile(r"\b(he|him|his)\b", re.I)
_FEMALE_PRONOUNS = re.compile(r"\b(she|her|hers)\b", re.I)


class HeuristicAttributor:
    """See module docstring. Stateless between calls; state lives per-book.

    After :meth:`attribute`, :attr:`genders` holds a best-effort ``name -> 'm'
    | 'f'`` guess (characters with no clear signal are omitted), used to cast
    gender-appropriate voices.
    """

    def __init__(self) -> None:
        self.genders: dict[str, str] = {}
        # How each dialogue line was resolved, keyed by ``(chapter.index,
        # position in chapter.segments)``. Kept beside the segments (not on
        # Segment) so the shared model stays free of attribution internals.
        self.tiers: dict[tuple[int, int], str] = {}

    def attribute(self, chapters: list[Chapter]) -> list[str]:
        self.tiers = {}
        discovered: dict[str, None] = {}  # insertion-ordered set of names
        for chapter in chapters:
            self._attribute_chapter(chapter, discovered)
        names = self.merge_aliases(list(discovered), chapters)
        self.infer_genders(names, chapters)
        return names

    def infer_genders(self, names: list[str], chapters: list[Chapter]) -> dict[str, str]:
        """Vote each character's gender from pronouns in their dialogue tags.

        For every line a character speaks, the surrounding tag ("...he said")
        usually carries a matching pronoun. Majority vote across the book is a
        reliable signal for main characters, who speak most. Public so another
        attributor can recompute genders after it changes speakers.
        """
        known = set(names)
        votes: dict[str, list[int]] = {n: [0, 0] for n in names}
        for chapter in chapters:
            segs = chapter.segments
            for i, seg in enumerate(segs):
                if seg.kind != SegmentKind.DIALOGUE or seg.speaker not in known:
                    continue
                tag = self._neighbor_narration(segs, i + 1) + " " + self._neighbor_narration(
                    segs, i - 1
                )
                votes[seg.speaker][0] += len(_MALE_PRONOUNS.findall(tag))
                votes[seg.speaker][1] += len(_FEMALE_PRONOUNS.findall(tag))

        self.genders = {}
        for name, (male, female) in votes.items():
            if male > female:
                self.genders[name] = "m"
            elif female > male:
                self.genders[name] = "f"
        return self.genders

    @staticmethod
    def merge_aliases(names: list[str], chapters: list[Chapter]) -> list[str]:
        """Fold obvious nicknames into their full name (``Neph`` -> ``Nephis``).

        Rule: a short single-word name that is a case-insensitive prefix of a
        longer known name is treated as the same character. Cheap and safe; the
        harder cases (unrelated nicknames) are left for an LLM attributor.
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

        if not canonical:
            return names
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
            return segments[j].text
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
