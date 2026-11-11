"""Measure character-gender resolution and voice casting on the Shadow Slave gold.

    .venv/Scripts/python.exe evals/gender/build_gold.py            # once, from the local gold
    .venv/Scripts/python.exe evals/gender/run_eval.py              # both slices, slice evidence
    .venv/Scripts/python.exe evals/gender/run_eval.py --scope book # evidence from all 100 chapters

Each slice is run twice on the same attribution output: ``before`` (the
dialogue-tag pronoun vote and the any-gender voice pool, reproduced here from the
old code) and ``after`` (the layered resolver and the unresolved-to-narrator
rule). It reports, per slice:

* character gender accuracy among the characters the resolver committed to, and
  coverage (how many characters it committed to);
* characters with the wrong gender, and speakers the gold does not know;
* dialogue lines per voice gender (m / f / narrator voice);
* lines voiced by a wrong-gender voice. The speaker for this count is the
  ATTRIBUTED speaker (what the pipeline would actually voice), judged against the
  gold gender of that character. A line attributed to the wrong person is
  therefore only caught where the spot-check labels (``gold/speaker-labels.json``)
  cover it: those are reported separately, with speaker accuracy per tier.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from unittest import mock

import common

from book2audio.attribution import HeuristicAttributor
from book2audio.attribution import heuristic as heuristic_module
from book2audio.models import NARRATOR, Chapter, SegmentKind
from book2audio.tts.voices import (
    CHARACTER_VOICE_POOL,
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    MALE_VOICES,
    VoiceCast,
)

SPEAKER_LABELS = os.path.join(common.GOLD_DIR, "speaker-labels.json")
_MALE = re.compile(r"\b(he|him|his)\b", re.I)
_FEMALE = re.compile(r"\b(she|her|hers)\b", re.I)


# --- the behaviour before this change, reproduced for the comparison --------------------


def legacy_infer_genders(names: list[str], chapters: list[Chapter]) -> dict[str, str]:
    """Old rule: count pronouns in the narration next to each dialogue line, per speaker."""
    known = set(names)
    votes = {n: [0, 0] for n in names}
    for chapter in chapters:
        segs = chapter.segments

        def neighbor(j: int) -> str:
            ok = 0 <= j < len(segs) and segs[j].kind == SegmentKind.NARRATION
            return segs[j].text if ok else ""

        for i, seg in enumerate(segs):
            if seg.kind != SegmentKind.DIALOGUE or seg.speaker not in known:
                continue
            tag = neighbor(i + 1) + " " + neighbor(i - 1)
            votes[seg.speaker][0] += len(_MALE.findall(tag))
            votes[seg.speaker][1] += len(_FEMALE.findall(tag))
    genders = {}
    for name, (male, female) in votes.items():
        if male > female:
            genders[name] = "m"
        elif female > male:
            genders[name] = "f"
    return genders


class LegacyVoiceCast:
    """Old casting: unknown gender draws from the full pool; uncast names hash into it."""

    def __init__(self, narrator_voice: str) -> None:
        self.narrator_voice = narrator_voice
        self._assigned: dict[str, str] = {}

    def cast(self, characters: list[str], genders: dict[str, str]) -> None:
        pools = {"m": list(MALE_VOICES), "f": list(FEMALE_VOICES), "any": list(CHARACTER_VOICE_POOL)}
        cursors = {"m": 0, "f": 0, "any": 0}
        for name in characters:
            key = genders.get(name, "any")
            pool = pools[key]
            self._assigned[name] = pool[cursors[key] % len(pool)]
            cursors[key] += 1

    def voice_for(self, speaker: str) -> str:
        if speaker == NARRATOR:
            return self.narrator_voice
        if speaker not in self._assigned:
            digest = hashlib.sha1(speaker.encode("utf-8")).digest()
            self._assigned[speaker] = CHARACTER_VOICE_POOL[digest[0] % len(CHARACTER_VOICE_POOL)]
        return self._assigned[speaker]


# --- running one variant ----------------------------------------------------------------


@dataclass
class Variant:
    name: str
    chapters: list[Chapter]
    tiers: dict[tuple[int, int], str]
    genders: dict[str, str]
    voice_for: Callable[[str], str]
    verdicts: dict = field(default_factory=dict)


def _order_by_frequency(names: list[str], chapters: list[Chapter]) -> list[str]:
    counts = collections.Counter(
        s.speaker for c in chapters for s in c.segments if s.kind == SegmentKind.DIALOGUE
    )
    return sorted(names, key=lambda n: (-counts[n], n))


def run_variant(name: str, scope_chapters: list[Chapter]) -> Variant:
    """Attribute ``scope_chapters`` (a private copy) and cast voices the old or new way."""
    import copy

    chapters = copy.deepcopy(scope_chapters)
    attributor = HeuristicAttributor()
    if name == "before":
        with mock.patch.object(heuristic_module, "_differ_by_one_vowel", lambda a, b: False):
            names = attributor.attribute(chapters)
        genders = legacy_infer_genders(names, chapters)
        cast = LegacyVoiceCast(DEFAULT_NARRATOR_VOICE)
        cast.cast(_order_by_frequency(names, chapters), genders)
        return Variant(name, chapters, attributor.tiers, genders, cast.voice_for)
    names = attributor.attribute(chapters)
    cast = VoiceCast()
    cast.cast(_order_by_frequency(names, chapters), attributor.genders)
    return Variant(
        name, chapters, attributor.tiers, dict(attributor.genders), cast.voice_for, attributor.verdicts
    )


@dataclass
class SliceResult:
    characters: list[dict] = field(default_factory=list)
    unmapped: dict[str, int] = field(default_factory=dict)
    phantom: dict[str, int] = field(default_factory=dict)
    lines_per_voice: collections.Counter = field(default_factory=collections.Counter)
    wrong_voice_lines: int = 0
    wrong_voice_by_speaker: collections.Counter = field(default_factory=collections.Counter)
    narrator_on_gold_female: int = 0
    lines_total: int = 0


def score_slice(variant: Variant, slice_chapters: list[Chapter], gold: common.Gold) -> SliceResult:
    result = SliceResult()
    lines = collections.Counter()
    by_char: dict[str, common.GoldCharacter | None] = {}
    for chapter in slice_chapters:
        # The variant owns its own deep copy: look the chapter up by parser index.
        own = next(c for c in variant.chapters if c.index == chapter.index)
        for seg in own.segments:
            if seg.kind != SegmentKind.DIALOGUE:
                continue
            result.lines_total += 1
            speaker = seg.speaker or NARRATOR
            voice = variant.voice_for(speaker)
            bucket = common.voice_bucket(voice, DEFAULT_NARRATOR_VOICE)
            result.lines_per_voice[bucket] += 1
            if speaker == NARRATOR:
                continue
            lines[speaker] += 1
            character = by_char.setdefault(speaker, gold.find(speaker))
            truth = character.gender if character else None
            if truth in ("m", "f"):
                if bucket in ("m", "f") and bucket != truth:
                    result.wrong_voice_lines += 1
                    result.wrong_voice_by_speaker[speaker] += 1
                if bucket == "narrator" and truth == "f":
                    result.narrator_on_gold_female += 1

    for speaker, count in lines.most_common():
        character = by_char[speaker]
        resolved = variant.genders.get(speaker)
        if character is None:
            result.unmapped[speaker] = count
        elif character.approx_dialogue_lines == 0:
            result.phantom[speaker] = count
        result.characters.append(
            {
                "name": speaker,
                "lines": count,
                "gold": character.gender if character else None,
                "resolved": resolved,
                "confidence": (
                    variant.verdicts[speaker].confidence if speaker in variant.verdicts else None
                ),
                "scored": bool(character and character.gender in ("m", "f")),
            }
        )
    return result


def gender_accuracy(result: SliceResult) -> dict:
    scored = [c for c in result.characters if c["scored"]]
    resolved = [c for c in scored if c["resolved"]]
    correct = [c for c in resolved if c["resolved"] == c["gold"]]
    return {
        "characters_scored": len(scored),
        "resolved": len(resolved),
        "correct": len(correct),
        "accuracy_among_resolved": (len(correct) / len(resolved)) if resolved else None,
        "coverage": (len(resolved) / len(scored)) if scored else None,
        "wrong": [c["name"] for c in resolved if c["resolved"] != c["gold"]],
        "unresolved": [c["name"] for c in scored if not c["resolved"]],
    }


# --- speaker spot-check labels ------------------------------------------------------------


def load_speaker_labels() -> dict[str, str]:
    if not os.path.isfile(SPEAKER_LABELS):
        return {}
    with open(SPEAKER_LABELS, encoding="utf-8") as fh:
        return json.load(fh)["labels"]


def line_id(chapter_index: int, ordinal: int, text: str) -> str:
    normalized = " ".join(text.split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{chapter_index}:{ordinal}:{digest}"


def score_spot_check(variant: Variant, labels: dict[str, str], gold_by_slice: dict) -> dict:
    """Speaker accuracy per heuristic tier, and wrong-gender voicing on the labelled lines."""
    per_tier: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    wrong_voice = scored_voice = 0
    for chapter in variant.chapters:
        ordinal = 0
        for position, seg in enumerate(chapter.segments):
            if seg.kind != SegmentKind.DIALOGUE:
                continue
            ordinal += 1
            key = line_id(chapter.index, ordinal, seg.text)
            if key not in labels:
                continue
            truth = labels[key]
            tier = variant.tiers.get((chapter.index, position), "?")
            per_tier[tier][1] += 1
            same = _same_person(seg.speaker or NARRATOR, truth, gold_by_slice)
            per_tier[tier][0] += int(same)
            true_gender = _gold_gender(truth, gold_by_slice)
            if true_gender in ("m", "f"):
                scored_voice += 1
                bucket = common.voice_bucket(
                    variant.voice_for(seg.speaker or NARRATOR), DEFAULT_NARRATOR_VOICE
                )
                wrong_voice += int(bucket in ("m", "f") and bucket != true_gender)
    return {"per_tier": dict(per_tier), "wrong_voice": wrong_voice, "scored_voice": scored_voice}


def _gold_gender(name: str, gold_by_slice: dict) -> str | None:
    for gold in gold_by_slice.values():
        character = gold.find(name)
        if character:
            return character.gender
    return None


def _same_person(attributed: str, truth: str, gold_by_slice: dict) -> bool:
    if attributed.casefold() == truth.casefold():
        return True
    for gold in gold_by_slice.values():
        a, b = gold.find(attributed), gold.find(truth)
        if a is not None and a is b:
            return True
    return False


# --- output -------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def print_slice(label: str, variant: Variant, result: SliceResult) -> None:
    acc = gender_accuracy(result)
    print(f"  [{variant.name}]")
    print(
        f"    characters scored {acc['characters_scored']}, resolved {acc['resolved']} "
        f"(coverage {_pct(acc['coverage'])}), correct {acc['correct']} "
        f"(accuracy among resolved {_pct(acc['accuracy_among_resolved'])})"
    )
    print(f"    wrong-gender characters: {acc['wrong'] or 'none'}")
    print(f"    unresolved (gold m/f): {acc['unresolved'] or 'none'}")
    print(f"    speakers not in the gold (not scored): {dict(result.unmapped) or 'none'}")
    print(f"    phantom speakers (gold: speaks 0 lines): {dict(result.phantom) or 'none'}")
    voices = result.lines_per_voice
    print(
        f"    dialogue lines {result.lines_total}: male voice {voices['m']}, "
        f"female voice {voices['f']}, narrator voice {voices['narrator']}"
    )
    print(
        f"    lines voiced by a wrong-gender voice (attributed speaker vs gold): "
        f"{result.wrong_voice_lines} {dict(result.wrong_voice_by_speaker) or ''}"
    )
    print(
        f"    gold-female speakers' lines read by the narrator voice: {result.narrator_on_gold_female}"
    )
    rows = sorted(result.characters, key=lambda c: -c["lines"])[:8]
    for c in rows:
        conf = "" if c["confidence"] is None else f" conf {c['confidence']:.2f}"
        print(f"      {c['name']:<16} {c['lines']:>3} lines  gold={c['gold']}  resolved={c['resolved']}{conf}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scope", choices=["slice", "book"], default="slice",
                        help="evidence from the slice chapters only, or from the whole book")
    parser.add_argument("--slice", choices=["all", *common.SLICES], default="all")
    parser.add_argument("--epub", default=common.EPUB_PATH)
    parser.add_argument("--json", help="also write the numbers to this file")
    args = parser.parse_args(argv)

    book = common.load_book(args.epub)
    names = list(common.SLICES) if args.slice == "all" else [args.slice]
    gold_by_slice = {n: common.load_gold(n) for n in common.SLICES}
    labels = load_speaker_labels()
    summary: dict = {}

    for slice_name in names:
        spec = common.SLICES[slice_name]
        slice_chapters = common.chapters_in(book, spec)
        scope = slice_chapters if args.scope == "slice" else [c for c in book]
        print(f"\n=== {slice_name} (book chapters {spec.first}-{spec.last}), evidence scope: {args.scope} ===")
        summary[slice_name] = {}
        for name in ("before", "after"):
            variant = run_variant(name, scope)
            result = score_slice(variant, slice_chapters, gold_by_slice[slice_name])
            print_slice(slice_name, variant, result)
            summary[slice_name][name] = {
                **gender_accuracy(result),
                "lines_per_voice": dict(result.lines_per_voice),
                "wrong_voice_lines": result.wrong_voice_lines,
                "lines_total": result.lines_total,
            }

    if labels:
        print("\n=== speaker spot-check (labelled lines, both slices, slice-scoped attribution) ===")
        for name in ("before", "after"):
            variants = [
                run_variant(name, common.chapters_in(book, spec)) for spec in common.SLICES.values()
            ]
            totals: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
            wrong_voice = scored_voice = 0
            for variant in variants:
                spot = score_spot_check(variant, labels, gold_by_slice)
                for tier, (right, count) in spot["per_tier"].items():
                    totals[tier][0] += right
                    totals[tier][1] += count
                wrong_voice += spot["wrong_voice"]
                scored_voice += spot["scored_voice"]
            overall = [sum(v[0] for v in totals.values()), sum(v[1] for v in totals.values())]
            population = collections.Counter(t for v in variants for t in v.tiers.values())
            weighted = sum(
                population[tier] * totals[tier][0] / totals[tier][1] for tier in totals
            ) / sum(population[tier] for tier in totals)
            print(f"  [{name}] speaker accuracy by tier:")
            for tier in sorted(totals):
                right, count = totals[tier]
                print(f"    {tier:<16} {right}/{count} = {_pct(right / count)}")
            print(f"    overall          {overall[0]}/{overall[1]} = {_pct(overall[0] / overall[1])}"
                  " (sample is stratified: equal lines per tier)")
            print(
                f"    estimated over all {sum(population.values())} dialogue lines "
                f"(tier accuracy weighted by how common each tier is): {_pct(weighted)}"
            )
            print(f"    tier sizes: {dict(population)}")
            print(
                f"    labelled lines (true speaker gold m/f) voiced by a wrong-gender voice: "
                f"{wrong_voice}/{scored_voice}"
            )
            summary.setdefault("spot_check", {})[name] = {
                "per_tier": dict(totals),
                "wrong_voice": wrong_voice,
                "scored_voice": scored_voice,
            }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
