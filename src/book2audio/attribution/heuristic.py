"""Deterministic, offline speaker attribution.

Strategy, in priority order, for each dialogue segment:

0. A quote with no closing punctuation inside a sentence ("the word "honor"
   again") is a scare quote, not speech: the narrator voices it.
1. A dialogue *tag* in the narration touching the quote — "he said", "asked
   Sunny", "Nephis whispered". The name must be the *subject* of the speech
   verb: in "Nephis asked Sunny" or "in front of Sunny chuckled" the second
   name is not the speaker. A trailing tag must sit in the quote's own
   paragraph; the next paragraph's narration introduces the next quote.
2. If the tag only has a pronoun ("he said"), resolve it to the most recent
   speaker the pronoun can refer to (the book's gender evidence is used when
   it is clear). A tag whose subject is an unnamed person ("The old policeman
   grunted") makes the narrator voice the line instead of a named character.
3. No tag at all: assume a two-person back-and-forth and alternate between the
   last two distinct speakers.

This is not perfect — it is a strong, cheap baseline that makes dialogue-heavy
chapters clearly follow-able. An LLM attributor can replace it later.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from book2audio.attribution.gender import (
    GenderVerdict,
    resolve_genders,
    resolved_genders,
)
from book2audio.attribution.gender import _split_sentences as split_sentences
from book2audio.models import NARRATOR, Chapter, Segment, SegmentKind

# Verbs that introduce speech. Kept broad; false positives are cheap here.
_SPEECH_VERBS = (
    "said|asked|replied|answered|shouted|muttered|whispered|cried|added|sighed|"
    "murmured|exclaimed|growled|yelled|continued|snapped|called|demanded|"
    "breathed|laughed|hissed|spoke|declared|announced|repeated|responded|"
    "gasped|screamed|roared|grumbled|chuckled|noted|remarked|stated|began|"
    "interrupted|offered|warned|agreed|insisted|admitted|observed|scoffed"
)

# Verbs that introduce speech only when a subject precedes them: "Nephis told
# Sunny" is a tag, but there is no "told Sunny" inversion that names the speaker.
_SUBJECT_ONLY_VERBS = "told|grunted"

# Abbreviated titles end in a period. Without handling them the name pattern
# stops at that period and "Mrs. Schofield" is captured as just "Mrs".
_TITLE = r"(?:(?:Mr|Mrs|Ms|Dr|St|Prof|Capt|Col|Gen|Rev|Messrs)\.\s+)?"

# A proper name: an optional abbreviated title, then one or two capitalized
# words. Avoids all-caps shouting.
_NAME = _TITLE + r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"

_SPEECH = rf"(?:{_SPEECH_VERBS}|{_SUBJECT_ONLY_VERBS})\b"
_ADVERBS = r"(?:[a-z]+ly\s+)?"
# Plain words between a subject and its speech verb: "looked at him and, after a pause, asked".
# A pronoun inside the gap would be another clause's subject ("she couldn't see him, he said").
_GAP = r"(?:(?!(?:he|she|they|it|I|we)\b)[a-z']+,?\s+){0,8}?"
# "Quietly she said" must not read "Quietly" as a name.
_PRONOUNS = r"(?:he|she|they|it|I|we|you|his|her|their)\b"

# "said Sunny" / "asked the Changing Star": the verb opens the sentence (or a
# clause), so the name after it is the speaker. "she asked Sunny" is not this
# shape: there the verb has its own subject and Sunny is who was asked.
_INVERSION = re.compile(
    rf"(?:^|[,;—]\s*)(?:(?:and|then|but)\s+)?{_ADVERBS}(?:{_SPEECH_VERBS})\b\s+(?:the\s+)?({_NAME})"
)
# "Sunny said" / "Nephis tilted her head and whispered": a name opens the clause
# and a speech verb follows within a few plain words. Group 1 is the name.
_SUBJECT = re.compile(
    rf"(?:^|[,;—]\s*|\b(?:and|but|then|as|when|while|after|before|until|though)\s+)"
    rf"(?:[Tt]he\s+)?({_NAME})(?!\s+{_PRONOUNS})\s+{_GAP}{_SPEECH}"
)
# The subject of a sentence that ends in a colon or is a short action beat:
# "Shifty was the first one to speak:", "Cassia opened her eyes wide."
_SUBJECT_ANY = re.compile(
    rf"(?:^|[,;—]\s*)(?:[Tt]he\s+)?({_NAME})(?!\s+{_PRONOUNS})\s+[a-z]"
)
# "he said", "she softly asked", "she turned to Sunny and said".
_PRONOUN_SUBJECT = re.compile(rf"\b(he|she|they)\b\s+{_GAP}{_SPEECH}", re.I)
# "The old policeman grunted.": a described, unnamed person is the subject.
_UNNAMED_SUBJECT = re.compile(rf"^(?:The|An?)\s+(?:[A-Za-z'-]+\s+){{0,6}}?{_SPEECH}")
# "called" also means "named". Passive ("was called Rain") and descriptive ("a
# girl called Rain") naming introduce a character, not a speaker, so these
# phrases are blanked out before any tag is matched. Real speech keeps its
# shape: '"Hey," Sunny called.', 'Sunny called out', '"Come!" called the guard'.
_NAMING_PHRASE = re.compile(
    r"\b(?:(?:was|is|were|are|am|be|been|being)\s+(?:\w+\s+)?"
    r"|(?:a|an|the|his|her|their|my|your|our|its)\s+(?:[A-Za-z'-]+,?\s+){0,3})"
    r"called\b"
)
# A vocative inside the quote names who is spoken TO: "Sunny, wait." / "Wait, Sunny."
_VOCATIVE_FIRST = re.compile(rf"^({_NAME})[,!]")
_VOCATIVE_LAST = re.compile(rf",\s+({_NAME})[.!?…]*$")

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
# "Cat" is the start of "Caterpillar" and a character of its own; a nickname that
# short is not safe to fold. "Neph" for "Nephis" is the shortest we trust.
MIN_NICKNAME_LENGTH = 4


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


def _paragraph_texts(chapters: list[Chapter]) -> list[str]:
    """Each paragraph's text (its segments joined); a segment of unknown paragraph stands alone."""
    paragraphs: list[str] = []
    for chapter in chapters:
        current: list[str] = []
        current_id: int | None = None
        for seg in chapter.segments:
            if seg.paragraph is None or seg.paragraph != current_id:
                if current:
                    paragraphs.append(" ".join(current))
                current, current_id = [], seg.paragraph
            current.append(seg.text)
        if current:
            paragraphs.append(" ".join(current))
    return paragraphs


def _mentioned_together(first: str, second: str, chapters: list[Chapter]) -> bool:
    """True when some paragraph of the book names both ``first`` and ``second``."""
    pattern_first = re.compile(rf"(?<!\w){re.escape(first)}(?!\w)")
    pattern_second = re.compile(rf"(?<!\w){re.escape(second)}(?!\w)")
    return any(
        first in text
        and second in text
        and pattern_first.search(text)
        and pattern_second.search(text)
        for text in _paragraph_texts(chapters)
    )


def _speak_in_one_scene(first: str, second: str, chapters: list[Chapter]) -> bool:
    """True when both have a line in the same scene (between scene breaks)."""
    for chapter in chapters:
        speakers: set[str] = set()
        for seg in [*chapter.segments, Segment(SegmentKind.SCENE_BREAK, "")]:
            if seg.kind == SegmentKind.SCENE_BREAK:
                if first in speakers and second in speakers:
                    return True
                speakers = set()
            elif seg.kind == SegmentKind.DIALOGUE and seg.speaker in (first, second):
                speakers.add(seg.speaker)
    return False


def _gender_conflict(names: list[str], chapters: list[Chapter]) -> bool:
    """True when two names are both resolved from the text, to opposite genders."""
    verdicts = resolve_genders(names, chapters)
    return {v.gender for v in verdicts.values()} >= {"m", "f"}


# A title or kinship word first makes a name a person ("Aunt Polly"), not an epithet.
_PERSON_WORDS = frozenset(
    "aunt uncle doctor dr mr mrs ms miss mister madam madame dame lady lord sir master "
    "professor prof captain capt colonel col general gen reverend rev father mother "
    "brother sister king queen prince princess saint st grandfather grandmother "
    "grandpa grandma cousin".split()
)
# What may follow "Name, the Epithet" without turning it into a list of people.
_LIST_CONTINUATION = re.compile(r"\s*(?:,|\band\b|\bor\b)\s+(?:the\s+)?[A-Z]")


def epithet_shapes(epithet: str, name: str) -> re.Pattern[str]:
    """The text shapes that tie an epithet to a name. The comma shapes need "the":

    ``Nephis, the Changing Star`` is an appositive; ``Penrod, Rupe Collins`` and
    ``Tom, Aunt Polly and Sid`` are lists of people.
    """
    e, n = re.escape(epithet), re.escape(name)
    # "Epithet Name" ("Changing Star Nephis") only means anything for a multi-word
    # epithet; two single words side by side are just two words.
    adjacent = rf"|(?<!\w){e}\s+{n}(?!\w)" if " " in epithet else ""
    return re.compile(
        rf"(?<!\w){n},\s+the\s+{e}(?!\w)"
        rf"|(?<!\w)[Tt]he\s+{e},\s+{n}(?!\w)"
        + adjacent
        + rf"|(?<!\w){n},?\s+(?:also\s+)?(?:known|called|named|titled|dubbed)\s+as\s+(?:the\s+)?{e}(?!\w)"
    )


def _tied_by_text(epithet: str, name: str, narration: list[str]) -> bool:
    shapes = epithet_shapes(epithet, name)
    for text in narration:
        # Plain substring tests first: almost no segment holds both names.
        if epithet not in text or name not in text:
            continue
        for match in shapes.finditer(text):
            if not _LIST_CONTINUATION.match(text, match.end()):
                return True
    return False


def _epithet_links(names: list[str], chapters: list[Chapter]) -> list[tuple[str, str]]:
    """``(epithet, name)`` pairs the narration itself ties together.

    The epithet has two or more words ("Changing Star") and is not a person's
    name ("Aunt Polly", "Rupe Collins"); the name is a one-word speaker. Both
    must already be speakers, so nothing new is invented. The link needs one of
    these shapes in the text: ``Name, the Epithet``, ``the Epithet, Name``,
    ``Epithet Name`` and ``Name, known as the Epithet``, not followed by more
    names in a list, and the two must not be of opposite gender.
    """
    singles = [n for n in names if " " not in n]
    epithets = [
        n
        for n in names
        if " " in n
        and n.split()[0].lower().rstrip(".") not in _PERSON_WORDS
        and not set(n.split()) & set(singles)  # "Georgie Bassett" holds a speaker's name
    ]
    if not epithets or not singles:
        return []
    narration = [
        seg.text for c in chapters for seg in c.segments if seg.kind == SegmentKind.NARRATION
    ]
    links: list[tuple[str, str]] = []
    for epithet in epithets:
        for name in singles:
            if _tied_by_text(epithet, name, narration) and not _gender_conflict(
                [epithet, name], chapters
            ):
                links.append((epithet, name))
    return links


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
        # Gender evidence from the previous pass, so "she said" can skip a male
        # speaker. Empty on the first pass.
        self._hint: dict[str, str] = {}
        self._hint_aliases: dict[str, str] = {}
        self._known: dict[str, None] = {}  # names discovered so far in this pass
        self._prior: set[str] = set()  # names the previous pass found

    def attribute(self, chapters: list[Chapter]) -> list[str]:
        """Attribute every dialogue segment, in two passes.

        Genders are read from the narration, not from who was assigned a line, so
        the first pass can feed the second: a pronoun tag ("she said") then only
        refers to a speaker of that gender.
        """
        self._hint, self._hint_aliases, self._prior = {}, {}, set()
        names = self._attribute_pass(chapters)
        if self.genders:
            self._hint = {**_leaning_genders(self.verdicts), **self.genders}
            self._hint_aliases = dict(self.aliases)
            self._prior = set(self._known)
            names = self._attribute_pass(chapters)
            self._hint, self._hint_aliases, self._prior = {}, {}, set()
        return names

    def _attribute_pass(self, chapters: list[Chapter]) -> list[str]:
        self.tiers = {}
        self.aliases = {}
        self._known = {}
        for chapter in chapters:
            self._attribute_chapter(chapter, self._known)
        names = self.merge_aliases(list(self._known), chapters)
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
        same chapter, because two real characters do share scenes. A pair that
        does speak in one chapter is still merged when no sentence anywhere
        mentions both: real characters get named together, a spelling slip does
        not. A final ``a``/``o`` swap (Maria/Mario) is never merged. The one
        speaking more lines wins.

        An epithet the text itself ties to a name is the same character:
        ``Nephis, the Changing Star``, ``Changing Star Nephis``, ``Nephis, known as
        the Changing Star``. The single-word name wins. Every fold is recorded in
        :attr:`aliases` and points at its final target.

        Known limit: two different characters with names like this who never
        speak in the same chapter (a Julie in chapter 3 and a Julia in chapter
        40), or who share a chapter but are never named together, are still
        merged.
        """
        canonical: dict[str, str] = {}
        for short in names:
            if " " in short or len(short) < MIN_NICKNAME_LENGTH:
                continue
            for full in names:
                if full != short and " " not in full and len(full) > len(short):
                    if full.lower().startswith(short.lower()) and not _gender_conflict(
                        [short, full], chapters
                    ):
                        canonical[short] = full
                        break

        lines, chapters_of = _speaker_stats(chapters)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                if first in canonical or second in canonical:
                    continue
                if not _differ_by_one_vowel(first, second):
                    continue
                if chapters_of.get(first, set()) & chapters_of.get(second, set()) and (
                    _speak_in_one_scene(first, second, chapters)
                    or _mentioned_together(first, second, chapters)
                ):
                    continue
                if _gender_conflict([first, second], chapters):
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

        for epithet, name in _epithet_links(names, chapters):
            if epithet in canonical or name in canonical or epithet in canonical.values():
                continue
            canonical[epithet] = name

        canonical = _resolve_targets(canonical)
        if not canonical:
            return names
        # An earlier call may already have folded X into Y; if Y now folds into Z,
        # X must point at Z, not at a name that no longer speaks.
        final = _resolve_targets({**self.aliases, **canonical})
        self.aliases = final
        for chapter in chapters:
            for seg in chapter.segments:
                if seg.speaker in canonical:
                    seg.speaker = final[seg.speaker]
        return [n for n in names if n not in canonical]

    def drop_aliases_still_speaking(self, speakers: list[str]) -> None:
        """Forget folds whose alias is a speaker again, so ``aliases`` matches the labels."""
        for alias in [a for a in self.aliases if a in speakers]:
            del self.aliases[alias]

    def fold_aliases(self, pairs: list[tuple[str, str]], chapters: list[Chapter]) -> None:
        """Fold ``(alias, name)`` pairs found by another source (the AI alias judge).

        Of each pair the one-word name wins ("Nephis" over "Changing Star"), else
        the one with more dialogue. A name that already absorbed another is never
        folded away, and each fold points at its final target in :attr:`aliases`.
        """
        lines, _ = _speaker_stats(chapters)
        canonical = dict(self.aliases)
        for first, second in pairs:
            if first in canonical or second in canonical:
                continue
            single = [n for n in (first, second) if " " not in n]
            if len(single) == 1:
                winner = single[0]
            else:
                # On a tie the second of the pair (the model's "name") wins.
                winner = first if lines.get(first, 0) > lines.get(second, 0) else second
            loser = second if winner == first else first
            if loser in canonical.values():
                continue
            canonical[loser] = winner
        canonical = _resolve_targets(canonical)
        self.aliases = canonical
        for chapter in chapters:
            for seg in chapter.segments:
                if seg.speaker in canonical:
                    seg.speaker = canonical[seg.speaker]

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
        ``named_leading``, ``continuation``, ``pronoun``, ``unnamed``,
        ``quoted_term``, ``alternation`` or ``fallback``. The first three are
        explicit evidence in the text; the rest are guesses or "not a character".
        """
        after_idx, before_idx = i + 1, i - 1
        trailing = _trailing_tag(segments, i)
        before_free = before_idx not in consumed
        leading = (
            _leading_tag(segments, i, lambda n: n in recent or self._is_known(n))
            if before_free
            else None
        )
        # A tag after the quote is consumed so the next quote does not read it as
        # its own lead-in. Pronoun and unnamed tags are only consumed when the
        # paragraph says the tag really belongs to this quote.
        consume = None
        if trailing is not None and (
            trailing.kind == "named" or _same_paragraph(segments[i], segments[after_idx])
        ):
            consume = after_idx

        # 1. Trailing named tag wins ("..." said Sunny).
        if trailing is not None and trailing.kind == "named":
            return trailing.name or NARRATOR, consume, "named_trailing"
        # 2. Leading named tag (Sunny said, "...") — not a previous line's tag.
        if leading is not None and leading.kind == "named":
            return leading.name or NARRATOR, None, "named_leading"

        # 3. '"A," said Sunny, "B."': the tag between two quotes of one paragraph
        # speaks for both.
        if not before_free and _continues_previous_quote(segments, i):
            previous = segments[i - 2].speaker
            if previous:
                return previous, consume, "continuation"

        # 4. Pronoun-only tag -> the recent speaker the pronoun can refer to.
        pronoun = next((t for t in (trailing, leading) if t and t.kind == "pronoun"), None)
        if pronoun is not None:
            speaker = self._resolve_pronoun(
                pronoun, _narration_names(segments, i), recent, segments[i].text
            )
            if speaker != NARRATOR:
                return speaker, consume, "pronoun"
            return NARRATOR, consume, "fallback"

        # 5. A described, unnamed speaker ("The old policeman grunted") is not
        # any named character: the narrator voice reads the line.
        if any(t is not None and t.kind == "unnamed" for t in (trailing, leading)):
            return NARRATOR, consume, "unnamed"

        # 6. A quote that is part of the sentence ("the word "honor" again").
        if trailing is None and (leading is None or leading.kind == "beat"):
            if _is_quoted_term(segments, i):
                return NARRATOR, None, "quoted_term"

        # 7. The narration just before the quote is a short action by one named
        # character ("Cassia opened her eyes wide."): usually they speak next.
        if leading is not None and leading.kind == "beat":
            return leading.name or NARRATOR, None, "named_beat"

        # 8. Untagged: alternate between the last two speakers if we have them.
        addressee = _addressee(segments[i].text)
        if len(recent) >= 2:
            chosen = recent[-2]
            if chosen == addressee:  # spoken TO them, so someone else is talking
                chosen = recent[-1] if recent[-1] != addressee else NARRATOR
            return chosen, None, "alternation"
        if recent:
            return (NARRATOR if recent[-1] == addressee else recent[-1]), None, "fallback"
        return NARRATOR, None, "fallback"  # unknown speaker falls back to the narrator voice

    def _resolve_pronoun(
        self, tag: _Tag, mentioned: _Mentions, recent: list[str], quote: str
    ) -> str:
        """The speaker a bare "he said" / "she asked" points at.

        Without gender evidence this is the most recent speaker. With it, a
        pronoun can only refer to someone of that gender, names in the narration
        next to the quote are tried first, and a pronoun nobody fits (a woman
        speaks, only men have) is a stranger: the narrator voice.
        """
        addressee = _addressee(quote)
        if not self._hint:
            return recent[-1] if recent and recent[-1] != addressee else NARRATOR
        candidates = [n for n in reversed(mentioned.near) if self._is_known(n)]
        candidates += reversed(recent)
        candidates += [n for n in reversed(mentioned.wider) if self._is_known(n)]
        for name in candidates:
            if name != addressee and self._fits(name, tag.gender):
                return name
        return NARRATOR

    def _is_known(self, name: str) -> bool:
        return name in self._known or name in self._prior

    def _fits(self, name: str, gender: str | None) -> bool:
        """False only when the gender evidence says this character is the other one."""
        if gender is None:
            return True
        known = self._hint.get(self._hint_aliases.get(name, name))
        return known is None or known == gender


@dataclass(frozen=True)
class _Tag:
    """What a dialogue tag in the narration says about the speaker."""

    kind: str  # "named" | "pronoun" | "unnamed"
    name: str | None = None  # the speaker, for "named"
    gender: str | None = None  # "m" | "f" | None, for "pronoun"


LEAN_MIN_EVIDENCE = 2.0
LEAN_MIN_SHARE = 0.6


def _leaning_genders(verdicts: Mapping[str, GenderVerdict]) -> dict[str, str]:
    """Characters whose evidence leans one way without reaching the voice threshold.

    Not good enough to pick a voice, but enough to say that "he said" is probably
    not about a character the text keeps calling "she".
    """
    leaning: dict[str, str] = {}
    for name, verdict in verdicts.items():
        total = verdict.male_weight + verdict.female_weight
        majority = max(verdict.male_weight, verdict.female_weight)
        if total >= LEAN_MIN_EVIDENCE and majority / total >= LEAN_MIN_SHARE:
            leaning[name] = "m" if verdict.male_weight > verdict.female_weight else "f"
    return leaning


def _same_paragraph(first: Segment, second: Segment) -> bool:
    """True when both came from one paragraph; unknown paragraphs count as the same.

    Hand-built segments carry no paragraph, and treating them as adjacent keeps
    the old behaviour for them.
    """
    return first.paragraph is None or second.paragraph is None or first.paragraph == second.paragraph


def _narration(segments: list[Segment], j: int) -> str:
    if 0 <= j < len(segments) and segments[j].kind == SegmentKind.NARRATION:
        return _NAMING_PHRASE.sub(" ", segments[j].text)
    return ""


def _trailing_tag(segments: list[Segment], i: int) -> _Tag | None:
    """The tag in the narration right after quote ``i``, if it can be this quote's.

    A tag belongs to the quote it touches: it must start in the quote's own
    paragraph, and a sentence that ends in a colon opens the NEXT quote.
    """
    text = _narration(segments, i + 1)
    if not text or not _same_paragraph(segments[i], segments[i + 1]):
        return None
    sentences = split_sentences(text)
    if not sentences or sentences[0].rstrip().endswith(":"):
        return None
    return _tag_in_sentence(sentences[0])


def _leading_tag(
    segments: list[Segment], i: int, is_known: Callable[[str], bool]
) -> _Tag | None:
    """The tag in the narration right before quote ``i`` (its last two sentences)."""
    sentences = split_sentences(_narration(segments, i - 1))
    for sentence in reversed(sentences[-2:]):
        tag = _tag_in_sentence(sentence, is_known)
        if tag is not None:
            return tag
    return None


def _tag_in_sentence(
    sentence: str, is_known: Callable[[str], bool] | None = None
) -> _Tag | None:
    """The speaker a sentence names.

    ``is_known`` is given for the sentence right before a quote: one that ends in
    a colon names its subject even without a speech verb, and a short action beat
    ("Cassia opened her eyes wide.") hints at it. Neither has a verb to prove the
    capitalized word is a name, so it must already be a known character."""
    for pattern in (_INVERSION, _SUBJECT):
        name = _first_name(pattern, sentence)
        if name:
            return _Tag("named", name=name)
    if is_known is not None and sentence.rstrip().endswith(":"):
        name = _first_name(_SUBJECT_ANY, sentence, is_known)
        if name:
            return _Tag("named", name=name)
    pronoun = _PRONOUN_SUBJECT.search(sentence)
    if pronoun:
        return _Tag("pronoun", gender=_PRONOUN_GENDER.get(pronoun.group(1).lower()))
    if _UNNAMED_SUBJECT.match(sentence):
        return _Tag("unnamed")
    if is_known is not None and len(sentence.split()) <= MAX_BEAT_WORDS:
        names = [n for n in _names_in(sentence) if is_known(n)]
        if len(set(names)) == 1 and _first_name(_SUBJECT_ANY, sentence, is_known) == names[0]:
            return _Tag("beat", name=names[0])
    return None


def _first_name(
    pattern: re.Pattern[str], sentence: str, is_known: Callable[[str], bool] | None = None
) -> str | None:
    for match in pattern.finditer(sentence):
        name = match.group(1).strip()
        if name.split()[0] not in _STOPWORDS and (is_known is None or is_known(name)):
            return name
    return None


MAX_BEAT_WORDS = 14


_PRONOUN_GENDER = {"he": "m", "she": "f"}


@dataclass(frozen=True)
class _Mentions:
    """Names in the narration around a quote: touching it, and a little further back."""

    near: list[str]
    wider: list[str]


MENTION_LOOKBACK = 6  # segments before the quote that still show who is in the scene


def _names_in(text: str) -> list[str]:
    return [
        m.group().strip()
        for m in re.finditer(_NAME, text)
        if m.group().split()[0] not in _STOPWORDS
    ]


def _subject_names(text: str) -> list[str]:
    """Names that open a clause ("Nephis sighed"), not ones used as objects ("to Shifty")."""
    return [
        name
        for sentence in split_sentences(text)
        for match in _SUBJECT_ANY.finditer(sentence)
        if (name := match.group(1).strip()).split()[0] not in _STOPWORDS
    ]


def _narration_names(segments: list[Segment], i: int) -> _Mentions:
    # The narration after the quote only describes this quote when it shares its paragraph.
    touching = [i - 1] + ([i + 1] if _same_paragraph(segments[i], segments[min(i + 1, len(segments) - 1)]) else [])
    near = _subject_names(" ".join(_narration(segments, j) for j in touching))
    wider = _subject_names(
        " ".join(_narration(segments, j) for j in range(max(0, i - MENTION_LOOKBACK), i - 1))
    )
    return _Mentions(near, wider)


def _continues_previous_quote(segments: list[Segment], i: int) -> bool:
    return (
        i >= 2
        and segments[i - 2].kind == SegmentKind.DIALOGUE
        and segments[i - 2].paragraph is not None
        and segments[i - 2].paragraph == segments[i].paragraph
    )


_NO_TERMINAL = frozenset(".!?…,;:—–-")
_LEAD_IN = re.compile(rf"(?:\b{_SPEECH}|[:,])\s*$")
MAX_SCARE_QUOTE_WORDS = 6


def _is_quoted_term(segments: list[Segment], i: int) -> bool:
    """A short quote with no closing punctuation, in the middle of a sentence.

    Real speech ends with punctuation inside the quotes; ``the word "honor"``
    does not. A lead-in right before it ("Sunny said "yes"") keeps it speech.
    """
    text = segments[i].text.rstrip()
    if not text or text[-1] in _NO_TERMINAL or len(text.split()) > MAX_SCARE_QUOTE_WORDS:
        return False
    before = segments[i - 1].text.rstrip() if i > 0 and segments[i - 1].kind == SegmentKind.NARRATION else ""
    return not _LEAD_IN.search(before)


def _addressee(quote: str) -> str | None:
    """The name a quote is spoken to ("Sunny, wait." / "Wait, Sunny."), if any."""
    for pattern in (_VOCATIVE_FIRST, _VOCATIVE_LAST):
        match = pattern.search(quote.strip())
        if match and match.group(1).split()[0] not in _STOPWORDS:
            return match.group(1)
    return None
