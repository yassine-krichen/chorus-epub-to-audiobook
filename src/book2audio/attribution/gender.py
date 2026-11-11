"""Layered character-gender resolver, with the evidence behind every verdict.

Wrong-gender voices confuse listeners, so a guess is only used when the evidence
is strong enough; otherwise the character stays *unresolved* and the voice rules
fall back to the narrator. Inference alone cannot reach 100%: the goal is to be
right when it commits and to say "unknown" otherwise.

Layers, all read from narration only (never from inside a quote):

* **L1 pronouns** - ``he/his/himself`` and ``she/her/hers/herself`` credited to
  the nearest preceding known name in the same sentence. A pronoun that comes
  before any name in its sentence is credited, at half weight, to the previous
  sentence of the same narration run when that sentence names exactly one known
  character. Object ``him`` is never counted, and ``her`` only when it reads as a
  possessive ("her chin"), not an object ("told her.").
* **L2 titles and roles** - ``Mrs. X``, ``Lady X``, ``his sister X``,
  ``X, the girl``. ``Master`` is deliberately not gendered ("Master Jet" can be a
  woman). One such cue outweighs a handful of conflicting pronouns.
* **Overrides** - the user's word always wins, with confidence 1.0.

Pronouns are credited by proximity to a name in the text, never by who a
dialogue line was *assigned* to. A wrong or guessed speaker label therefore
cannot vote itself into a gender (the circular case the old dialogue-tag vote
had for ``pronoun``/``alternation``/``fallback`` lines).

Confidence is the Wilson 95% lower bound of the winning side's share of the
total evidence weight, so a few unanimous votes are not enough: with the
default threshold of 0.6, pronoun-only evidence needs about six agreeing
mentions, while a single title or kinship cue is enough by itself.

Known limit: Shadow Slave style narration is close third person on one
character, so "he" in a sentence about someone else leaks into their votes.
The majority and the Wilson bound absorb it for characters with a lot of text;
characters with little text stay unresolved. See ``evals/gender``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from book2audio.models import Chapter, SegmentKind

# Below this confidence a character is left unresolved (owner decision).
GENDER_CONFIDENCE_THRESHOLD = 0.6

# A title or kinship cue is worth this many ordinary pronoun votes.
STRONG_WEIGHT = 8.0
# A pronoun carried over from the previous sentence is a weaker link than one
# in the same sentence as the name.
CARRY_WEIGHT = 0.5

GENDER_OVERRIDE_MAX_ENTRIES = 100
GENDER_OVERRIDE_MAX_NAME_LENGTH = 80

_MAX_SNIPPET = 200
# Most evidence items kept per character; weights and counts cover all of them.
_MAX_KEPT_EVIDENCE = 40
_WILSON_Z = 1.96

PRONOUN = "pronoun"
TITLE = "title"
KINSHIP = "kinship"
APPOSITIVE = "appositive"
OVERRIDE = "override"

_GENDERS = frozenset("mf")

# Not gendered on purpose: Master, Dr, Prof, Captain, Count (a verb and a noun).
_TITLE_GENDER = {
    "mr": "m", "mister": "m", "sir": "m", "lord": "m", "king": "m", "prince": "m",
    "duke": "m", "baron": "m",
    "mrs": "f", "ms": "f", "miss": "f", "lady": "f", "queen": "f", "princess": "f",
    "duchess": "f", "baroness": "f", "madam": "f", "madame": "f", "dame": "f",
}
_KINSHIP_GENDER = {
    "brother": "m", "father": "m", "son": "m", "husband": "m", "uncle": "m",
    "nephew": "m", "grandfather": "m", "grandson": "m", "boyfriend": "m",
    "sister": "f", "mother": "f", "daughter": "f", "wife": "f", "aunt": "f",
    "niece": "f", "grandmother": "f", "granddaughter": "f", "girlfriend": "f",
}
_APPOSITIVE_GENDER = {
    "boy": "m", "man": "m", "gentleman": "m", "lad": "m", "prince": "m", "king": "m",
    "girl": "f", "woman": "f", "lady": "f", "lass": "f", "maiden": "f",
    "princess": "f", "queen": "f",
}

_PRONOUN = re.compile(r"\b(he|his|himself|she|hers|herself|her)\b", re.I)
_PRONOUN_GENDER = {
    "he": "m", "his": "m", "himself": "m",
    "she": "f", "hers": "f", "herself": "f", "her": "f",
}
# After these (or punctuation, or the end of the sentence) "her" is an object:
# "told her to wait", "looked at her.", "gave her and Sunny".
_OBJECT_FOLLOWERS = frozenset(
    "a an the to in on at for with from by of and or but that as if when while so then "
    "about into onto over up down out away again before after until because since than "
    "this these those".split()
)

# Single-quoted inner thoughts ('She is lying.') are speech-like and often about
# someone else; apostrophes inside words ("Sunny's") are not at a word boundary.
_THOUGHT = re.compile(r"(?<!\w)['\u2018][^'\u2019\n]{2,}?['\u2019](?!\w)")

_ABBREVIATIONS = ("Mr", "Mrs", "Ms", "Dr", "St", "Prof", "Capt", "Col", "Gen", "Rev", "Messrs")
_SENTENCE_END = re.compile(
    "(?<=[.!?\u2026])[\"')\\]\u201d\u2019]*\\s+(?=[A-Z\"'\u201c\u2018\\[(])"
)
_ABBREVIATION_TAIL = max(len(a) for a in _ABBREVIATIONS) + 2  # the word, its period, one more
# An abbreviation's period must not end a sentence: "Mr. Williams" stays whole.
_ABBREVIATED = re.compile(r"\b(?:%s)\.$" % "|".join(_ABBREVIATIONS))


@dataclass(frozen=True)
class GenderEvidence:
    """One reason to think a character is male or female."""

    layer: str  # pronoun | title | kinship | appositive | override
    gender: str  # "m" | "f"
    weight: float
    snippet: str  # the sentence the cue came from, at most 200 characters
    chapter_index: int


@dataclass(frozen=True)
class GenderVerdict:
    """The resolved gender of one character, or ``None`` when it is not safe to say."""

    name: str
    gender: str | None  # "m" | "f" | None (unresolved)
    confidence: float  # Wilson 95% lower bound of the winner's share; 1.0 for overrides
    evidence: tuple[GenderEvidence, ...]  # strongest first, capped; see the weights below
    male_weight: float = 0.0
    female_weight: float = 0.0
    layer_counts: tuple[tuple[str, int], ...] = ()  # evidence items per layer, uncapped


def wilson_lower_bound(successes: float, total: float, z: float = _WILSON_Z) -> float:
    """Lower end of the Wilson score interval for ``successes`` out of ``total``."""
    if total <= 0:
        return 0.0
    share = successes / total
    denominator = 1 + z * z / total
    centre = share + z * z / (2 * total)
    margin = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def validate_gender_overrides(overrides: Mapping[str, str]) -> None:
    """Raise ``ValueError`` unless ``overrides`` is a small ``name -> 'm' | 'f'`` map."""
    if len(overrides) > GENDER_OVERRIDE_MAX_ENTRIES:
        raise ValueError(f"at most {GENDER_OVERRIDE_MAX_ENTRIES} gender overrides are allowed")
    for name, gender in overrides.items():
        if not name.strip() or len(name) > GENDER_OVERRIDE_MAX_NAME_LENGTH:
            raise ValueError(
                f"gender override names must be 1 to {GENDER_OVERRIDE_MAX_NAME_LENGTH} characters"
            )
        if gender not in _GENDERS:
            raise ValueError(f"gender override for {name!r} must be 'm' or 'f', got {gender!r}")


def resolved_genders(verdicts: Mapping[str, GenderVerdict]) -> dict[str, str]:
    """The ``name -> 'm' | 'f'`` map of characters that are not unresolved."""
    return {name: v.gender for name, v in verdicts.items() if v.gender is not None}


def resolve_genders(
    names: Iterable[str],
    chapters: list[Chapter],
    *,
    overrides: Mapping[str, str] | None = None,
    aliases: Mapping[str, str] | None = None,
    threshold: float = GENDER_CONFIDENCE_THRESHOLD,
) -> dict[str, GenderVerdict]:
    """Return a verdict for every name in ``names`` (in order).

    ``aliases`` maps a spelling variant to its canonical name (``Cassia`` ->
    ``Cassie``) so narration that uses the variant still counts. ``overrides``
    maps a name (or alias, case-insensitive) to ``'m'`` or ``'f'`` and wins.
    """
    names = list(names)
    aliases = dict(aliases or {})
    forced = _normalize_overrides(overrides or {}, names, aliases)
    matcher = _NameMatcher(names, aliases)
    tally: dict[str, _Tally] = {name: _Tally() for name in names}

    for chapter in chapters:
        for segment in chapter.segments:
            if segment.kind != SegmentKind.NARRATION:
                continue
            _collect_from_narration(segment.text, chapter.index, matcher, tally)

    verdicts: dict[str, GenderVerdict] = {}
    for name in names:
        if name in forced:
            verdicts[name] = _forced_verdict(name, forced[name])
        else:
            verdicts[name] = tally[name].verdict(name, threshold)
    return verdicts


# --- internals ------------------------------------------------------------------------


@dataclass
class _Tally:
    male: float = 0.0
    female: float = 0.0
    items: list[GenderEvidence] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, evidence: GenderEvidence) -> None:
        if evidence.gender == "m":
            self.male += evidence.weight
        else:
            self.female += evidence.weight
        self.counts[evidence.layer] = self.counts.get(evidence.layer, 0) + 1
        self.items.append(evidence)

    def verdict(self, name: str, threshold: float) -> GenderVerdict:
        total = self.male + self.female
        majority = max(self.male, self.female)
        confidence = wilson_lower_bound(majority, total)
        gender: str | None = None
        if self.male != self.female and confidence >= threshold:
            gender = "m" if self.male > self.female else "f"
        # Strong cues first, then pronouns in reading order; the weights above
        # already cover whatever the cap drops.
        ordered = sorted(self.items, key=lambda e: -e.weight)[:_MAX_KEPT_EVIDENCE]
        return GenderVerdict(
            name=name,
            gender=gender,
            confidence=round(confidence, 4),
            evidence=tuple(ordered),
            male_weight=self.male,
            female_weight=self.female,
            layer_counts=tuple(sorted(self.counts.items())),
        )


def _forced_verdict(name: str, gender: str) -> GenderVerdict:
    evidence = GenderEvidence(OVERRIDE, gender, 1.0, "set by the user", 0)
    return GenderVerdict(
        name=name,
        gender=gender,
        confidence=1.0,
        evidence=(evidence,),
        male_weight=1.0 if gender == "m" else 0.0,
        female_weight=1.0 if gender == "f" else 0.0,
        layer_counts=((OVERRIDE, 1),),
    )


def _normalize_overrides(
    overrides: Mapping[str, str], names: list[str], aliases: Mapping[str, str]
) -> dict[str, str]:
    """Map each override onto the canonical character it names (case-insensitive)."""
    by_lower = {name.lower(): name for name in names}
    aliases_lower = {variant.lower(): canonical for variant, canonical in aliases.items()}
    forced: dict[str, str] = {}
    for key, gender in overrides.items():
        key = key.strip()
        canonical = aliases_lower.get(key.lower(), key)
        target = by_lower.get(canonical.lower())
        if target is not None:
            forced[target] = gender
    return forced


class _NameMatcher:
    """Finds known character names (and their spelling variants) in a sentence."""

    def __init__(self, names: list[str], aliases: Mapping[str, str]) -> None:
        self._canonical: dict[str, str] = {}
        for name in names:
            self._canonical[name] = name
            # "Mrs. Schofield" is usually just "Schofield" after the first mention.
            bare = re.sub(r"^(?:%s)\.\s+" % "|".join(_ABBREVIATIONS), "", name)
            self._canonical.setdefault(bare, name)
        for variant, canonical in aliases.items():
            if canonical in self._canonical.values():
                self._canonical.setdefault(variant, canonical)
        spellings = sorted(self._canonical, key=len, reverse=True)
        alternation = "|".join(re.escape(s) for s in spellings)
        self.pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)") if spellings else None
        self._kinship = self._compile_kinship(alternation)
        self._appositive = self._compile_appositive(alternation)
        titles = "|".join(title.capitalize() for title in _TITLE_GENDER)
        self._title = (
            re.compile(rf"\b({titles})\.?\s+({alternation})(?!\w)") if spellings else None
        )

    def canonical(self, spelling: str) -> str:
        return self._canonical[spelling]

    @staticmethod
    def _compile_kinship(alternation: str) -> tuple[re.Pattern[str], re.Pattern[str]] | None:
        if not alternation:
            return None
        kin = "|".join(_KINSHIP_GENDER)
        owner = r"(?:his|her|their|my|our|[A-Z]\w*['\u2019]s)"
        modifiers = r"(?:[\w-]+\s+){0,2}?"
        # "his little sister was called Rain", "her mother, Cassie"
        forward = re.compile(
            rf"\b{owner}\s+{modifiers}({kin})\b(?!['\u2019]s)\s*,?\s*"
            rf"(?:(?:was|is)\s+)?(?:(?:called|named)\s+)?({alternation})(?!\w)"
        )
        # "Rain, his little sister" / "Rain was his sister"
        backward = re.compile(
            rf"(?<!\w)({alternation})\s*,?\s*(?:(?:was|is)\s+)?({owner})\s+{modifiers}({kin})\b"
            rf"(?!['\u2019]s)"
        )
        return forward, backward

    @staticmethod
    def _compile_appositive(alternation: str) -> re.Pattern[str] | None:
        if not alternation:
            return None
        words = "|".join(_APPOSITIVE_GENDER)
        # "Nephis, the silver-haired girl"
        return re.compile(
            rf"(?<!\w)({alternation}),\s+(?:the|a|an)\s+(?:[\w-]+\s+){{0,2}}?({words})\b(?![\w'\u2019-])"
        )

    def title_matches(self, sentence: str) -> list[tuple[str, str]]:
        """``(canonical name, gender)`` for every ``Mrs. X``-style mention."""
        if self._title is None:
            return []
        return [
            (self._canonical[m.group(2)], _TITLE_GENDER[m.group(1).lower()])
            for m in self._title.finditer(sentence)
        ]

    def kinship_matches(self, sentence: str) -> list[tuple[str, str, int | None]]:
        """``(name, gender, position)`` for "his sister X" and "X, his sister".

        ``position`` is where the owner word of an "X, his sister" match starts:
        that "his" belongs to someone else, so it must not also vote for X.
        """
        if self._kinship is None:
            return []
        forward, backward = self._kinship
        found: list[tuple[str, str, int | None]] = [
            (self._canonical[m.group(2)], _KINSHIP_GENDER[m.group(1)], None)
            for m in forward.finditer(sentence)
        ]
        found += [
            (self._canonical[m.group(1)], _KINSHIP_GENDER[m.group(3)], m.start(2))
            for m in backward.finditer(sentence)
        ]
        return found

    def appositive_matches(self, sentence: str) -> list[tuple[str, str]]:
        if self._appositive is None:
            return []
        return [
            (self._canonical[m.group(1)], _APPOSITIVE_GENDER[m.group(2)])
            for m in self._appositive.finditer(sentence)
        ]


def _split_sentences(text: str) -> list[str]:
    text = _THOUGHT.sub(" ", text)
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        # Only the last few characters can hold an abbreviation; scanning from
        # `start` would make a long run of "Mr. A" quadratic.
        if _ABBREVIATED.search(text[max(start, match.start() - _ABBREVIATION_TAIL) : match.start()]):
            continue
        sentences.append(text[start : match.start()].strip())
        start = match.end()
    sentences.append(text[start:].strip())
    return [s for s in sentences if s]


def _snippet(sentence: str) -> str:
    sentence = " ".join(sentence.split())
    return sentence if len(sentence) <= _MAX_SNIPPET else sentence[: _MAX_SNIPPET - 1] + "\u2026"


def _is_possessive(match: re.Match[str], sentence: str) -> bool:
    """True when this "her" reads as a possessive ("her chin"), not an object ("told her.")."""
    following = re.match(r"\s*([A-Za-z]+)", sentence[match.end() :])
    return following is not None and following.group(1).lower() not in _OBJECT_FOLLOWERS


def _pronoun_credits(
    sentence: str,
    mentions: list[tuple[int, str]],
    carry_target: str | None,
    ignored_positions: set[int],
) -> list[tuple[str, str, float]]:
    """``(name, gender, weight)`` for each pronoun that refers to a known character.

    A pronoun belongs to the nearest name before it in the sentence. Object
    ``him`` is never counted. Refinements that skipped names after a preposition
    ("with Nephis, he ...") or after an object pronoun ("gave him his sword")
    were measured on the Shadow Slave gold and did not reduce the leakage from
    Sunny's point of view (see ``evals/gender``), so they are not kept.
    """
    credits: list[tuple[str, str, float]] = []
    for pronoun in _PRONOUN.finditer(sentence):
        if pronoun.start() in ignored_positions:
            continue
        if pronoun.group(1).lower() == "her" and not _is_possessive(pronoun, sentence):
            continue
        gender = _PRONOUN_GENDER[pronoun.group(1).lower()]
        earlier = [name for position, name in mentions if position < pronoun.start()]
        if earlier:
            credits.append((earlier[-1], gender, 1.0))
        elif carry_target is not None:
            credits.append((carry_target, gender, CARRY_WEIGHT))
    return credits


def _collect_from_narration(
    text: str, chapter_index: int, matcher: _NameMatcher, tally: dict[str, _Tally]
) -> None:
    previous_names: list[str] = []
    for sentence in _split_sentences(text):
        snippet = _snippet(sentence)

        def add(name: str, layer: str, gender: str, weight: float) -> None:
            tally[name].add(GenderEvidence(layer, gender, weight, snippet, chapter_index))

        for name, gender in matcher.title_matches(sentence):
            add(name, TITLE, gender, STRONG_WEIGHT)
        foreign_owners: set[int] = set()
        for name, gender, owner_position in matcher.kinship_matches(sentence):
            add(name, KINSHIP, gender, STRONG_WEIGHT)
            if owner_position is not None:
                foreign_owners.add(owner_position)
        for name, gender in matcher.appositive_matches(sentence):
            add(name, APPOSITIVE, gender, STRONG_WEIGHT)

        mentions = (
            [(m.start(), matcher.canonical(m.group())) for m in matcher.pattern.finditer(sentence)]
            if matcher.pattern is not None
            else []
        )
        # Only a previous sentence about exactly one known character is a safe
        # referent for a pronoun that opens this one.
        carry_target = previous_names[0] if len(set(previous_names)) == 1 else None

        credits = _pronoun_credits(sentence, mentions, carry_target, foreign_owners)
        for name, gender, weight in credits:
            add(name, PRONOUN, gender, weight)
        previous_names = [name for _, name in mentions]
