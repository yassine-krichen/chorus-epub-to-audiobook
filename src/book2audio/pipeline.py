"""End-to-end orchestration: EPUB in, audiobook out.

This wires the stages together and owns the cross-cutting concerns — progress
reporting and resume — that individual stages shouldn't know about. The stages
themselves stay pure and independently testable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from typing import Callable

import numpy as np
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from book2audio.assemble.audio import concat_with_gaps, normalize_rms, write_wav
from book2audio.assemble.audiobook import ChapterAudio, build_m4b
from book2audio.assemble.music import LOOP_SECONDS, generate_bed_for_preset, mix_music_bed
from book2audio.attribution import LLMAttributor, build_attributor
from book2audio.config import Config
from book2audio.epub import ParsedChapter, parse_epub
from book2audio.models import Book, BookMetadata, Chapter, Segment, SegmentKind
from book2audio.state import RenderState
from book2audio.text import normalize_for_tts, segment_paragraphs, speech_params
from book2audio.tts import KokoroEngine, VoiceCast

# Map EPUB language codes to Kokoro's expected language tags.
_LANG_MAP = {"en": "en-us", "en-us": "en-us", "en-gb": "en-gb", "gb": "en-gb"}


def _plan_batches(
    chapters: list[Chapter], batch_size: int
) -> list[tuple[int, list[Chapter]]]:
    """Group chapters into ``(part_number, chapters)`` batches.

    ``batch_size <= 0`` (or a size covering everything) yields a single batch,
    i.e. one output file for the whole selection. Part numbers are 1-based.
    """
    if batch_size <= 0 or batch_size >= len(chapters):
        return [(1, list(chapters))]
    batches: list[tuple[int, list[Chapter]]] = []
    for i in range(0, len(chapters), batch_size):
        batches.append((len(batches) + 1, chapters[i : i + batch_size]))
    return batches


class Pipeline:
    def __init__(
        self,
        config: Config,
        console: Console | None = None,
        *,
        on_chapter_done: Callable[[Chapter], None] | None = None,
        on_part_ready: Callable[[int, str, list[Chapter]], None] | None = None,
        on_attribution_progress: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.console = console or Console()
        self._music_bed: np.ndarray | None = None
        self._on_chapter_done = on_chapter_done
        self._on_part_ready = on_part_ready
        # Attribution can run for many minutes before the first chapter
        # finishes, so hosted workers need a liveness signal from it.
        self._on_attribution_progress = on_attribution_progress

    def run(self) -> str:
        cfg = self.config
        cfg.validate()

        chapters_dir = os.path.join(cfg.work_dir, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)

        book = self._load_and_prepare()
        cast = self._cast_voices(book)
        state = self._init_state(book, cast)

        selected = self._select_chapters(book)
        self._report_plan(book, selected, cast)

        engine = self._load_engine(book.metadata)
        self._music_bed = generate_bed_for_preset(cfg.music_preset) if cfg.music else None
        outputs = self._render_and_assemble(
            selected, book.metadata, engine, cast, state, chapters_dir
        )

        self.console.print(f"[bold green]Done:[/bold green] {len(outputs)} file(s)")
        for path in outputs:
            self.console.print(f"  {path}")
        # Return the last file so single-file callers get the familiar path.
        return outputs[-1] if outputs else cfg.output_path

    # --- stages -----------------------------------------------------------

    def _load_and_prepare(self, *, estimate_only: bool = False) -> Book:
        """Parse and attribute the book.

        ``estimate_only`` (dry runs) keeps an LLM attributor from making any
        request: it labels with the heuristic and reports the windows it would send.
        """
        metadata, parsed = parse_epub(self.config.epub_path, cover_dir=self.config.work_dir)
        chapters = [self._build_chapter(pc) for pc in parsed]
        book = Book(metadata=metadata, chapters=chapters)

        # LLM calls only cover the chapters being rendered, so a --limit or
        # --start-chapter run does not spend the call budget on the rest.
        start_pos, end_pos = self.config.selection_bounds(len(chapters))
        selected = frozenset(c.index for c in chapters[start_pos:end_pos])
        attributor = build_attributor(
            self.config,
            only_chapters=selected,
            on_progress=None if estimate_only else self._on_attribution_progress,
        )
        if estimate_only and isinstance(attributor, LLMAttributor):
            characters = attributor.estimate(book.chapters)
        else:
            characters = attributor.attribute(book.chapters)
        self._characters = self._order_by_frequency(characters, book)
        self._genders = attributor.genders
        if isinstance(attributor, LLMAttributor):
            self._report_attribution(attributor, estimate_only)
        return book

    def _report_attribution(self, attributor: LLMAttributor, estimate_only: bool) -> None:
        report = attributor.report
        for warning in report.warnings:
            self.console.print(f"[yellow]Warning:[/yellow] {warning}")
        if estimate_only:
            self.console.print(
                f"[dim]LLM attribution ({report.mode}) estimate: {report.windows} window(s) "
                f"to send, {report.lines_targeted} line(s) targeted, budget "
                f"{self.config.attribution_max_calls} call(s); no request made[/dim]"
            )
        else:
            self.console.print(f"[dim]{report.summary()}[/dim]")

    @staticmethod
    def _order_by_frequency(characters: list[str], book: Book) -> list[str]:
        """Most-frequent speaker first, so the busiest leads get distinct voices."""
        counts: dict[str, int] = {name: 0 for name in characters}
        for chapter in book.chapters:
            for seg in chapter.segments:
                if seg.kind == SegmentKind.DIALOGUE and seg.speaker in counts:
                    counts[seg.speaker] += 1
        return sorted(characters, key=lambda n: (-counts[n], n))

    @staticmethod
    def _build_chapter(pc: ParsedChapter) -> Chapter:
        return Chapter(index=pc.index, title=pc.title, segments=segment_paragraphs(pc.paragraphs))

    def _cast_voices(self, book: Book) -> VoiceCast:
        cast = VoiceCast(
            narrator_voice=self.config.narrator_voice,
            overrides=dict(self.config.voice_overrides),
            male_voice=self.config.male_voice,
            female_voice=self.config.female_voice,
        )
        cast.cast(self._characters, getattr(self, "_genders", {}))
        return cast

    def _init_state(self, book: Book, cast: VoiceCast) -> RenderState:
        state = RenderState.load_or_new(
            os.path.join(self.config.work_dir, "state.json"),
            source=os.path.abspath(self.config.epub_path),
            title=book.metadata.title,
            narrator_voice=cast.narrator_voice,
        )
        state.cast = cast.as_dict()
        state.characters = self._characters
        state.save()
        return state

    def _select_chapters(self, book: Book) -> list[Chapter]:
        start_pos, end_pos = self.config.selection_bounds(len(book.chapters))
        chapters = book.chapters[start_pos:end_pos]
        if not chapters:
            raise ValueError(
                f"start_chapter={self.config.start_chapter} is beyond this book's "
                f"{len(book.chapters)} chapter(s); nothing to render"
            )
        return chapters

    def _load_engine(self, metadata: BookMetadata) -> KokoroEngine:
        lang = _LANG_MAP.get(metadata.language.lower(), "en-us")
        self.console.print(f"[dim]Loading Kokoro TTS ({lang})…[/dim]")
        return KokoroEngine(lang=lang)

    def _render_and_assemble(
        self,
        chapters: list[Chapter],
        metadata: BookMetadata,
        engine: KokoroEngine,
        cast: VoiceCast,
        state: RenderState,
        chapters_dir: str,
    ) -> list[str]:
        """Render chapters and mux each batch to a playable .m4b as it completes.

        Batches are assembled the moment their last chapter is rendered, so the
        first part is listenable long before the whole book finishes. A part is
        only (re)muxed when one of its chapters was freshly rendered this run or
        its file is missing — otherwise it's skipped for a fast resume.
        """
        batches = _plan_batches(chapters, self.config.batch_size)
        single = len(batches) == 1
        outputs: list[str] = []

        with self._progress() as progress:
            task = progress.add_task("Narrating", total=len(chapters))
            for part_no, batch in batches:
                batch_audio: list[ChapterAudio] = []
                dirty = False
                for chapter in batch:
                    wav_path = os.path.join(chapters_dir, f"chapter_{chapter.index:04d}.wav")
                    text_hash = self._chapter_hash(chapter, cast)
                    progress.update(task, description=f"Ch {chapter.index}: {chapter.title[:40]}")
                    if not state.is_done(chapter.index, wav_path, text_hash):
                        samples = self._render_chapter(chapter, engine, cast)
                        write_wav(wav_path, samples)
                        state.mark_done(chapter.index, chapter.title, text_hash)
                        dirty = True
                        if self._on_chapter_done is not None:
                            self._on_chapter_done(chapter)
                    batch_audio.append(ChapterAudio(title=chapter.title, wav_path=wav_path))
                    progress.advance(task)

                out_path = self._part_output(single, part_no, batch[0].index, batch[-1].index)
                if dirty or not os.path.exists(out_path):
                    part_meta = self._part_metadata(metadata, single, part_no, batch)
                    build_m4b(
                        batch_audio, part_meta, out_path, self.config.work_dir, self.config.bitrate
                    )
                    if self._on_part_ready is not None:
                        self._on_part_ready(part_no, out_path, batch)
                    self.console.print(f"[green]Part ready:[/green] {out_path}")
                outputs.append(out_path)
        return outputs

    def _part_output(self, single: bool, part_no: int, first: int, last: int) -> str:
        """Where a batch's .m4b goes. Single batch -> the plain output path."""
        if single:
            return self.config.output_path
        stem, ext = os.path.splitext(self.config.output_path)
        return f"{stem}-part-{part_no:02d}{ext}"

    @staticmethod
    def _part_metadata(
        metadata: BookMetadata, single: bool, part_no: int, batch: list[Chapter]
    ) -> BookMetadata:
        if single:
            return metadata
        title = f"{metadata.title} — Part {part_no} (Ch {batch[0].index}-{batch[-1].index})"
        return dataclasses.replace(metadata, title=title)

    def _chapter_hash(self, chapter: Chapter, cast: VoiceCast) -> str:
        """Fingerprint everything that affects a chapter's audio.

        Title, each segment's text, the voice/speed/pacing it will be spoken
        with, and every production setting (music, expressiveness, chapter
        gap) — so changing any of these re-renders only the affected chapters
        instead of serving stale audio.
        """
        cfg = self.config
        parts = [
            chapter.title,
            f"speed={cfg.speed}",
            f"chapter_gap={cfg.chapter_gap}",
            f"music={cfg.music}:{cfg.music_volume}:{cfg.music_preset}",
            f"expressive={cfg.expressive}",
        ]
        for seg in chapter.segments:
            if not seg.text:
                parts.append(seg.kind.value)
                continue
            voice = cast.voice_for(seg.speaker or "")
            speed_mult, gain_mult = speech_params(seg.text) if cfg.expressive else (1.0, 1.0)
            parts.append(f"{seg.kind.value}|{voice}|{speed_mult}|{gain_mult}|{seg.text}")
        blob = "␟".join(parts).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()

    def _render_chapter(
        self, chapter: Chapter, engine: KokoroEngine, cast: VoiceCast
    ) -> np.ndarray:
        cfg = self.config
        clips: list[tuple[np.ndarray, float]] = []

        # Spoken chapter heading in the narrator's voice.
        title_text = normalize_for_tts(chapter.title.replace(":", ". "))
        title_audio = engine.synthesize(title_text, cast.narrator_voice, cfg.speed)
        clips.append((normalize_rms(title_audio), cfg.title_gap))

        for seg in chapter.segments:
            if seg.kind == SegmentKind.SCENE_BREAK:
                clips.append((np.zeros(0, dtype=np.float32), cfg.scene_break_gap))
                continue
            voice = cast.voice_for(seg.speaker or "")
            text = normalize_for_tts(seg.text)
            if not text:
                continue

            speed_mult, gain_mult = speech_params(seg.text) if cfg.expressive else (1.0, 1.0)
            effective_speed = max(0.5, min(2.0, cfg.speed * speed_mult))
            samples = engine.synthesize(text, voice, effective_speed)
            samples = normalize_rms(samples) * gain_mult
            clips.append((samples.astype(np.float32), cfg.paragraph_gap))

        # Trailing silence marks the pause before the next chapter's title.
        clips.append((np.zeros(0, dtype=np.float32), cfg.chapter_gap))

        audio = concat_with_gaps(clips)
        if cfg.music and self._music_bed is not None:
            phase_offset = (chapter.index * 7) % LOOP_SECONDS
            audio = mix_music_bed(
                audio,
                self._music_bed,
                volume=cfg.music_volume,
                phase_offset_seconds=phase_offset,
            )
        return audio

    # --- reporting --------------------------------------------------------

    def _progress(self) -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        )

    def _report_plan(self, book: Book, selected: list[Chapter], cast: VoiceCast) -> None:
        c = self.console
        c.print(f"\n[bold]{book.metadata.title}[/bold] by {book.metadata.author}")
        words = sum(ch.word_count for ch in selected)
        c.print(
            f"Chapters: {len(selected)}/{len(book.chapters)}   "
            f"Words: {words:,}   Characters detected: {len(self._characters)}"
        )
        preview = ", ".join(self._characters[:8]) or "—"
        c.print(f"[dim]Cast preview: Narrator={cast.narrator_voice}; {preview}[/dim]")
