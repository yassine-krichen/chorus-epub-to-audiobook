"""The cast report: who speaks, what gender we concluded and why, and who voices them.

Built after attribution and gender resolution, printed in every run (and in
``--dry-run``) and saved as ``cast_report.json`` in the work directory so a
user can check the cast before listening. Characters that need a second look
(gender unresolved, or not confident) come first.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from book2audio.attribution.gender import GENDER_CONFIDENCE_THRESHOLD, GenderVerdict
from book2audio.models import Chapter, SegmentKind
from book2audio.tts.voices import VoiceCast

CAST_REPORT_FILENAME = "cast_report.json"
# What the website lists and lets the user fix; the API and the review UI use the same limits.
CAST_NAME_MAX_LENGTH = 80
CAST_MAX_CHARACTERS = 100


def cast_report_object_key(job_id: uuid.UUID) -> str:
    """Where a hosted job's cast report lives in storage (next to its other artifacts)."""
    return f"jobs/{job_id}/{CAST_REPORT_FILENAME}"


# The only TTS engine today; named per entry so a second engine can slot in.
VOICE_PROVIDER = "kokoro"
# A resolved gender below this confidence is still shown as "check this one".
REVIEW_CONFIDENCE = 0.8

_TOP_EVIDENCE = 3
_SAMPLE_LINE_CHARS = 120
_GENDER_WORD = {"m": "male", "f": "female", None: "unknown"}


@dataclass(frozen=True)
class CastEntry:
    name: str
    lines: int
    gender: str | None
    confidence: float
    needs_review: bool
    voice: str
    voice_source: str  # override | gender | narrator
    sample_line: str
    layers: Mapping[str, int] = field(default_factory=dict)
    evidence: tuple[Mapping[str, object], ...] = ()
    male_weight: float = 0.0
    female_weight: float = 0.0


def build_cast_report(
    characters: list[str],
    verdicts: Mapping[str, GenderVerdict],
    chapters: list[Chapter],
    cast: VoiceCast,
) -> list[CastEntry]:
    """One entry per character with dialogue in ``chapters``, review-first."""
    lines: dict[str, int] = {}
    sample: dict[str, str] = {}
    for chapter in chapters:
        for seg in chapter.segments:
            if seg.kind == SegmentKind.DIALOGUE and seg.speaker in characters:
                lines[seg.speaker] = lines.get(seg.speaker, 0) + 1
                sample.setdefault(seg.speaker, seg.text)

    entries = [
        _entry(name, lines[name], verdicts.get(name), sample[name], cast)
        for name in characters
        if name in lines
    ]
    entries.sort(key=lambda e: (not e.needs_review, -e.lines, e.name))
    return entries


def _entry(
    name: str, lines: int, verdict: GenderVerdict | None, sample: str, cast: VoiceCast
) -> CastEntry:
    gender = verdict.gender if verdict else None
    confidence = verdict.confidence if verdict else 0.0
    is_override = bool(verdict and verdict.evidence and verdict.evidence[0].layer == "override")
    if name in cast.overrides:
        source = "override"
    elif gender is not None:
        source = "gender"
    else:
        source = "narrator"
    evidence = verdict.evidence[:_TOP_EVIDENCE] if verdict else ()
    return CastEntry(
        name=name,
        lines=lines,
        gender=gender,
        confidence=confidence,
        needs_review=gender is None or (confidence < REVIEW_CONFIDENCE and not is_override),
        voice=cast.voice_for(name),
        voice_source=source,
        sample_line=_shorten(sample),
        layers=dict(verdict.layer_counts) if verdict else {},
        evidence=tuple(
            {
                "layer": e.layer,
                "gender": e.gender,
                "weight": e.weight,
                "chapter_index": e.chapter_index,
                "snippet": e.snippet,
            }
            for e in evidence
        ),
        male_weight=verdict.male_weight if verdict else 0.0,
        female_weight=verdict.female_weight if verdict else 0.0,
    )


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _SAMPLE_LINE_CHARS:
        return text
    return text[: _SAMPLE_LINE_CHARS - 3] + "..."


def reviewable_entries(entries: list[CastEntry]) -> tuple[list[CastEntry], int]:
    """The entries the website can list and fix, and how many were left out.

    The review changes characters by name and is bounded (names up to
    ``CAST_NAME_MAX_LENGTH``, ``CAST_MAX_CHARACTERS`` rows), so a character with a longer
    name, or one past the busiest ones, is not listed. Entries arrive review-first then
    busiest-first, so what is dropped is the quietest tail. Those characters still get
    their voice; they just cannot be edited.
    """
    listed = [e for e in entries if len(e.name) <= CAST_NAME_MAX_LENGTH]
    kept = listed[:CAST_MAX_CHARACTERS]
    return kept, len(entries) - len(kept)


def cast_report_to_dict(
    entries: list[CastEntry], *, voice_mode: str, narrator_voice: str
) -> dict[str, object]:
    entries, omitted = reviewable_entries(entries)
    return {
        "omitted_characters": omitted,
        "voice_mode": voice_mode,
        "narrator_voice": narrator_voice,
        "provider": VOICE_PROVIDER,
        "confidence_threshold": GENDER_CONFIDENCE_THRESHOLD,
        "characters": [
            {
                "name": e.name,
                "lines": e.lines,
                "gender": e.gender,
                "confidence": e.confidence,
                "needs_review": e.needs_review,
                "layer_counts": dict(e.layers),
                "male_weight": e.male_weight,
                "female_weight": e.female_weight,
                "top_evidence": [dict(item) for item in e.evidence],
                "sample_line": e.sample_line,
                "voice": e.voice,
                "voice_source": e.voice_source,
                "provider": VOICE_PROVIDER,
            }
            for e in entries
        ],
    }


def write_cast_report(
    work_dir: str, entries: list[CastEntry], *, voice_mode: str, narrator_voice: str
) -> str:
    path = os.path.join(work_dir, CAST_REPORT_FILENAME)
    payload = cast_report_to_dict(entries, voice_mode=voice_mode, narrator_voice=narrator_voice)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def print_cast_report(
    console: Console, entries: list[CastEntry], *, voice_mode: str, narrator_voice: str
) -> None:
    """Print the report; every name, line and snippet is escaped for Rich markup."""
    if voice_mode == "narrator_only":
        console.print(
            f"\n[bold]Cast:[/bold] narrator only, {escape(narrator_voice)} reads every line "
            "(no character voices)"
        )
        return
    if not entries:
        console.print("\n[bold]Cast:[/bold] no character dialogue in the selected chapters")
        return

    table = Table(title="Cast report (check the first rows)")
    table.add_column("Character")
    table.add_column("Lines", justify="right")
    table.add_column("Gender")
    table.add_column("Conf.", justify="right")
    table.add_column("Evidence")
    table.add_column("Voice")
    table.add_column("Sample line")
    for entry in entries:
        marker = "[yellow]?[/yellow] " if entry.needs_review else ""
        table.add_row(
            marker + escape(entry.name),
            str(entry.lines),
            _GENDER_WORD[entry.gender],
            f"{entry.confidence:.2f}",
            escape(_evidence_summary(entry)),
            escape(_voice_summary(entry)),
            escape(entry.sample_line[:60]),
        )
    console.print(table)
    unresolved = [e.name for e in entries if e.gender is None]
    if unresolved:
        console.print(
            f"[yellow]{len(unresolved)} character(s) have no clear gender and are read by "
            f"the narrator voice:[/yellow] {escape(', '.join(unresolved))}. "
            "Fix one with --gender NAME=m or NAME=f."
        )


def _evidence_summary(entry: CastEntry) -> str:
    if not entry.layers:
        return "no evidence"
    parts = ", ".join(f"{layer} {count}" for layer, count in sorted(entry.layers.items()))
    if entry.gender is None and (entry.male_weight or entry.female_weight):
        return f"{parts} (m {entry.male_weight:g} / f {entry.female_weight:g})"
    return parts


def _voice_summary(entry: CastEntry) -> str:
    if entry.voice_source == "narrator":
        return f"{entry.voice} (narrator)"
    if entry.voice_source == "override":
        return f"{entry.voice} (your choice)"
    return entry.voice
