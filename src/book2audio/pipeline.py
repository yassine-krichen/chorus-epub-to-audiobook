"""End-to-end orchestration: EPUB in, audiobook out.

This wires the stages together and owns the cross-cutting concerns — progress
reporting and resume — that individual stages shouldn't know about. The stages
themselves stay pure and independently testable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from collections.abc import Mapping
from typing import Callable

import numpy as np
from rich.console import Console
from rich.markup import escape
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
from book2audio.assemble.filenames import PartInfo, next_free_name, render_part_filenames
from book2audio.assemble.music import LOOP_SECONDS, generate_bed_for_preset, mix_music_bed
from book2audio.attribution import GenderVerdict, LLMAttributor, build_attributor
from book2audio.attribution.gender import resolve_genders, resolved_genders
from book2audio.cast_report import (
    build_cast_report,
    print_cast_report,
    record_clone_fallbacks,
    write_cast_report,
)
from book2audio.cast_review import merge_characters
from book2audio.config import Config
from book2audio.epub import ParsedChapter, parse_epub
from book2audio.models import NARRATOR, Book, BookMetadata, Chapter, Segment, SegmentKind
from book2audio.state import RenderState
from book2audio.text import normalize_for_tts, segment_paragraphs, speech_params
from book2audio.tts import KokoroEngine, VoiceCast
from book2audio.tts.clone_engine import (
    REASON_NOT_INSTALLED,
    REASON_VOICE_GONE,
    CloneReference,
    QwenCloneEngine,
    RoutingEngine,
)
from book2audio.tts.engine import TtsEngine
from book2audio.tts.voices import is_clone_voice

# Stored after a chapter's hash when Kokoro had to stand in for a cloned voice, so the
# chapter is rendered again once cloning works.
_CLONE_FALLBACK_MARK = "+clone-fallback"

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


def _unused_file_name(directory: str, name: str, claimed: set[str]) -> str:
    stem, extension = os.path.splitext(name)
    return next_free_name(
        stem,
        extension,
        lambda candidate: candidate.lower() in claimed
        or os.path.exists(os.path.join(directory, candidate)),
    )


def _recorded_file_name(name: str) -> str:
    """A file name read back from state.json, which is a plain file on disk.

    It is joined onto the output directory, so anything but a bare file name
    (a path, or "." / "..") is refused rather than followed.
    """
    is_bare = name == os.path.basename(name) and "/" not in name and "\\" not in name
    if not name or not is_bare or name in (".", ".."):
        raise ValueError(f"state.json records an unsafe output file name: {name!r}")
    return name


class Pipeline:
    def __init__(
        self,
        config: Config,
        console: Console | None = None,
        *,
        on_chapter_done: Callable[[Chapter], None] | None = None,
        on_part_ready: Callable[[int, str, list[Chapter]], None] | None = None,
        on_attribution_progress: Callable[[], None] | None = None,
        on_cast_ready: Callable[[list[str], dict[str, GenderVerdict]], None] | None = None,
        on_cast_report: Callable[[str], None] | None = None,
        clone_voices: Mapping[str, CloneReference] | None = None,
        clone_engine_factory: Callable[[], QwenCloneEngine | None] | None = None,
    ) -> None:
        self.config = config
        self.console = console or Console()
        self._music_bed: np.ndarray | None = None
        self._on_chapter_done = on_chapter_done
        self._on_part_ready = on_part_ready
        # Attribution can run for many minutes before the first chapter
        # finishes, so hosted workers need a liveness signal from it.
        self._on_attribution_progress = on_attribution_progress
        # Told who the characters are (most frequent speaker first) and what is
        # known of their gender, once the book is attributed and before any
        # chapter renders: the enhanced thumbnails need the cast.
        self._on_cast_ready = on_cast_ready
        # Handed the path of cast_report.json once it is written, before the
        # TTS model loads: hosted workers publish it so the job page can show the cast.
        self._on_cast_report = on_cast_report
        # Cloned voices the job names, with their reference clips, and how to start the clone
        # engine (None when this machine has none). Without an engine a cloned voice is read
        # by a Kokoro voice of its gender, and the cast report says so.
        self._clone_voices: dict[str, CloneReference] = dict(clone_voices or {})
        self._clone_engine_factory = clone_engine_factory
        self._clone_engine: QwenCloneEngine | None = None
        self._router: RoutingEngine | None = None
        self._report_path: str | None = None
        self._characters: list[str] = []
        self._genders: dict[str, str] = {}
        self._verdicts: dict[str, GenderVerdict] = {}
        self._aliases: dict[str, str] = {}

    def run(self) -> str:
        cfg = self.config
        cfg.validate()

        chapters_dir = os.path.join(cfg.work_dir, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)

        book = self._load_and_prepare()
        if self._on_cast_ready is not None:
            self._on_cast_ready(list(self._characters), dict(self._verdicts))
        cast = self._cast_voices(book)
        state = self._init_state(book, cast)

        selected = self._select_chapters(book)
        self._report_plan(book, selected, cast)
        self._clone_engine = self._start_clone_engine(cast)
        try:
            report_path = self.report_cast(selected, cast)
            if self._on_cast_report is not None:
                self._on_cast_report(report_path)

            engine = self._route_clone_voices(self._load_engine(book.metadata), cast)
            self._music_bed = generate_bed_for_preset(cfg.music_preset) if cfg.music else None
            outputs = self._render_and_assemble(
                selected, book.metadata, engine, cast, state, chapters_dir
            )
        finally:
            # The clone engine holds the GPU: release it as soon as the job is over.
            if self._router is not None:
                self._router.close()
            elif self._clone_engine is not None:
                self._clone_engine.close()
        self._record_runtime_clone_fallbacks()

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

        if self.config.voice_mode == "narrator_only":
            # One voice for everything: nothing to attribute, no characters to cast.
            for chapter in chapters:
                for seg in chapter.segments:
                    seg.speaker = NARRATOR
            self._characters, self._genders, self._verdicts = [], {}, {}
            return book

        # LLM calls only cover the chapters being rendered, so a --limit or
        # --start-chapter run does not spend the call budget on the rest.
        start_pos, end_pos = self.config.selection_bounds(chapters)
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
        self._verdicts = attributor.verdicts
        self._aliases = attributor.aliases
        if isinstance(attributor, LLMAttributor):
            self._report_attribution(attributor, estimate_only)
        self._apply_character_aliases(book)
        return book

    def _apply_character_aliases(self, book: Book) -> None:
        """Fold the characters the user merged in the cast review into their targets.

        Runs after attribution, on labels that are already cached, so a resumed hosted
        job pays for no new model call. Genders are worked out again for the merged cast,
        and the old spelling counts towards its target's evidence.
        """
        merged = merge_characters(book.chapters, self.config.character_aliases)
        if not merged:
            return
        survivors = [name for name in self._characters if name not in merged]
        joined = {source: target for source, target in merged.items() if target != NARRATOR}
        names = survivors + [t for t in dict.fromkeys(joined.values()) if t not in survivors]
        self._aliases = {**self._aliases, **joined}
        self._verdicts = resolve_genders(
            names,
            book.chapters,
            overrides=self.config.gender_overrides,
            aliases=self._aliases,
        )
        self._genders = resolved_genders(self._verdicts)
        self._characters = self._order_by_frequency(names, book)

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
            judges = attributor.judge_report
            for warning in judges.warnings:
                self.console.print(f"[yellow]Warning:[/yellow] {warning}")
            if judges.calls or judges.cache_hits:
                self.console.print(f"[dim]{judges.summary()}[/dim]")
                for reason in judges.reasons:
                    self.console.print(f"[dim]  dropped: {escape(reason)}[/dim]")

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
        return Chapter(
            index=pc.index,
            title=pc.title,
            segments=segment_paragraphs(pc.paragraphs),
            number=pc.number,
            front_matter=pc.front_matter,
        )

    def _cast_voices(self, book: Book) -> VoiceCast:
        cast = VoiceCast(
            narrator_voice=self.config.narrator_voice,
            overrides=dict(self.config.voice_overrides),
            male_voice=self.config.male_voice,
            female_voice=self.config.female_voice,
        )
        cast.cast(self._characters, self._genders)
        return cast

    def report_cast(self, selected: list[Chapter], cast: VoiceCast) -> str:
        """Print the cast report, save it as ``cast_report.json`` in the work dir, return its path."""
        entries = build_cast_report(self._characters, self._verdicts, selected, cast)
        mode, narrator = self.config.voice_mode, cast.narrator_voice
        print_cast_report(self.console, entries, voice_mode=mode, narrator_voice=narrator)
        fallbacks = self._planned_clone_fallbacks(cast)
        for item in fallbacks:
            self.console.print(
                f"[yellow]Warning:[/yellow] a cloned voice cannot be used ({escape(item['reason'])}); "
                f"{escape(item['fallback_voice'])} reads instead."
            )
        path = write_cast_report(
            self.config.work_dir,
            entries,
            voice_mode=mode,
            narrator_voice=narrator,
            clone_fallbacks=fallbacks,
        )
        self._report_path = path
        self.console.print(f"[dim]Cast report saved to {escape(path)}[/dim]")
        unmatched = self._unmatched_gender_overrides()
        if unmatched:
            self.console.print(
                "[yellow]Warning:[/yellow] --gender names that match no character: "
                f"{escape(', '.join(unmatched))}"
            )
        return path

    def _unmatched_gender_overrides(self) -> list[str]:
        if self.config.voice_mode == "narrator_only":
            return []  # no characters are detected, so every name would look unmatched
        known = {name.lower() for name in self._characters}
        # A merged spelling ("Cassia" for "Cassie") still names a character.
        known |= {variant.lower() for variant in self._aliases}
        return sorted(n for n in self.config.gender_overrides if n.strip().lower() not in known)

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
        start_pos, end_pos = self.config.selection_bounds(book.chapters)
        chapters = book.chapters[start_pos:end_pos]
        if not chapters:
            numbers = [c.number for c in book.chapters]
            raise ValueError(
                f"start_chapter={self.config.start_chapter} matches no chapter in this book "
                f"(its chapters are numbered {min(numbers)} to {max(numbers)}); "
                "nothing to render"
            )
        return chapters

    def _used_clone_voices(self, cast: VoiceCast) -> list[str]:
        """The cloned voices this job will speak with (narrator and characters), in order."""
        return list(dict.fromkeys(v for v in cast.as_dict().values() if is_clone_voice(v)))

    def _start_clone_engine(self, cast: VoiceCast) -> QwenCloneEngine | None:
        if not self._used_clone_voices(cast) or self._clone_engine_factory is None:
            return None
        return self._clone_engine_factory()

    def _planned_clone_fallbacks(self, cast: VoiceCast) -> list[dict[str, str]]:
        """Cloned voices that cannot be spoken before the render even starts, and why."""
        planned: list[dict[str, str]] = []
        for voice in self._used_clone_voices(cast):
            reference = self._clone_voices.get(voice)
            if reference is None:
                reason = REASON_VOICE_GONE
            elif self._clone_engine is None:
                reason = REASON_NOT_INSTALLED
            else:
                continue
            gender = reference.gender if reference is not None else "m"
            planned.append(
                {
                    "voice": voice,
                    "reason": reason,
                    "fallback_voice": RoutingEngine.fallback_for_gender(gender),
                }
            )
        return planned

    def _record_runtime_clone_fallbacks(self) -> None:
        """Note cloned voices that broke down during the render (out of memory, crash)."""
        if self._router is None or not self._router.fallbacks or self._report_path is None:
            return
        self.console.print(
            "[yellow]Warning:[/yellow] voice cloning failed during the render; Kokoro voices "
            "read the cloned parts that followed. See the cast report."
        )
        record_clone_fallbacks(self._report_path, self.clone_fallbacks)

    @property
    def clone_fallbacks(self) -> list[dict[str, str]]:
        """Every cloned voice replaced by a Kokoro voice in this run, with the reason."""
        if self._router is None:
            return []
        return [
            {
                "voice": voice,
                "reason": reason,
                "fallback_voice": self._router.fallback_voice(voice),
            }
            for voice, reason in self._router.fallbacks.items()
        ]

    def _load_engine(self, metadata: BookMetadata) -> KokoroEngine:
        lang = _LANG_MAP.get(metadata.language.lower(), "en-us")
        self.console.print(f"[dim]Loading Kokoro TTS ({lang})…[/dim]")
        return KokoroEngine(lang=lang)

    def _route_clone_voices(self, kokoro: TtsEngine, cast: VoiceCast) -> TtsEngine:
        """Kokoro alone, unless the job names a cloned voice: then route those voices."""
        if not self._used_clone_voices(cast):
            return kokoro
        self._router = RoutingEngine(kokoro, self._clone_voices, self._clone_engine)
        return self._router

    def _render_and_assemble(
        self,
        chapters: list[Chapter],
        metadata: BookMetadata,
        engine: TtsEngine,
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
        # Named up front, before anything is written, so duplicates are resolved
        # across all parts. None means the legacy <output>-part-NN naming.
        named_outputs = self._plan_named_outputs(batches, metadata, state)
        outputs: list[str] = []

        with self._progress() as progress:
            task = progress.add_task("Narrating", total=len(chapters))
            for part_no, batch in batches:
                batch_audio: list[ChapterAudio] = []
                dirty = False
                for chapter in batch:
                    wav_path = os.path.join(chapters_dir, f"chapter_{chapter.index:04d}.wav")
                    text_hash = self._chapter_hash(chapter, cast)
                    progress.update(
                        task, description=f"Ch {chapter.number}: {escape(chapter.title[:40])}"
                    )
                    if not state.is_done(chapter.index, wav_path, text_hash):
                        fallbacks_before = self._router.fallback_count if self._router else 0
                        samples = self._render_chapter(chapter, engine, cast)
                        write_wav(wav_path, samples)
                        if self._router is not None and self._router.fallback_count > fallbacks_before:
                            # Kokoro read cloned parts: a later run with the engine up must redo it.
                            text_hash += _CLONE_FALLBACK_MARK
                        state.mark_done(chapter.index, chapter.title, text_hash)
                        dirty = True
                        if self._on_chapter_done is not None:
                            self._on_chapter_done(chapter)
                    batch_audio.append(ChapterAudio(title=chapter.title, wav_path=wav_path))
                    progress.advance(task)

                if named_outputs is not None:
                    out_path = named_outputs[part_no]
                else:
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

    def _planned_file_names(
        self, batches: list[tuple[int, list[Chapter]]], metadata: BookMetadata
    ) -> list[str] | None:
        """Template-based file name per batch, or None for legacy naming."""
        template = self.config.resolved_filename_template()
        if template is None:
            return None
        extension = os.path.splitext(self.config.output_path)[1].lstrip(".") or "m4b"
        return render_part_filenames(
            template,
            title=metadata.title,
            parts=[PartInfo(no, batch[0].number, batch[0].title) for no, batch in batches],
            extension=extension,
        )

    def planned_file_names(self, chapters: list[Chapter], metadata: BookMetadata) -> list[str]:
        """File names a run over ``chapters`` would create (for dry runs).

        Empty with legacy naming, where the names are just the output path and
        ``-part-NN`` variants of it. Does not check for files already on disk.
        """
        batches = _plan_batches(chapters, self.config.batch_size)
        return self._planned_file_names(batches, metadata) or []

    def _plan_named_outputs(
        self,
        batches: list[tuple[int, list[Chapter]]],
        metadata: BookMetadata,
        state: RenderState,
    ) -> dict[int, str] | None:
        """Template-based file path per part number, or None for legacy naming.

        A part keeps the name recorded for it in ``state`` (same work dir resumed),
        even when a changed chapter makes it rebuild: it is rebuilt into its own
        file. A new part never takes a path that already exists (that file belongs
        to another run) or a name recorded for another part, so it gets " (2)",
        " (3)"...
        """
        names = self._planned_file_names(batches, metadata)
        if names is None:
            return None
        out_dir = os.path.dirname(self.config.output_path)

        # The planned name is part of the key: a changed template or title is a
        # new file, not a rename of the earlier one.
        keys = [
            f"{batch[0].index}-{batch[-1].index}:{name}"
            for (_, batch), name in zip(batches, names)
        ]
        # Recorded names are claimed first, so a fresh part cannot take the name
        # a later part already owns.
        recorded = {
            key: _recorded_file_name(state.part_files[key])
            for key in keys
            if key in state.part_files
        }
        claimed = {name.lower() for name in recorded.values()}

        paths: dict[int, str] = {}
        for (part_no, _), planned, key in zip(batches, names, keys):
            name = recorded.get(key)
            if name is None:
                name = _unused_file_name(out_dir, planned, claimed)
                claimed.add(name.lower())
                state.part_files[key] = name
            paths[part_no] = os.path.join(out_dir, name)
        state.save()
        return paths

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
        title = f"{metadata.title} — Part {part_no} (Ch {batch[0].number}-{batch[-1].number})"
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
            if self._router is not None:
                voice = self._router.resolve_voice(voice)  # the voice that will really speak
            speed_mult, gain_mult = speech_params(seg.text) if cfg.expressive else (1.0, 1.0)
            parts.append(f"{seg.kind.value}|{voice}|{speed_mult}|{gain_mult}|{seg.text}")
        blob = "␟".join(parts).encode("utf-8")
        return hashlib.sha1(blob).hexdigest()

    def _render_chapter(
        self, chapter: Chapter, engine: TtsEngine, cast: VoiceCast
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
        c.print(
            f"\n[bold]{escape(book.metadata.title)}[/bold] by {escape(book.metadata.author)}"
        )
        words = sum(ch.word_count for ch in selected)
        c.print(
            f"Chapters: {len(selected)}/{len(book.chapters)}   "
            f"Words: {words:,}   Characters detected: {len(self._characters)}"
        )
        preview = ", ".join(self._characters[:8]) or "—"
        c.print(f"[dim]Cast preview: Narrator={cast.narrator_voice}; {preview}[/dim]")
