"""Generation settings, validated at startup.

Per our security posture, config is validated the moment it's built — a bad
voice id or nonsensical speed fails immediately with a clear message, not
halfway through a 16-hour render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from book2audio.assemble.filenames import (
    default_filename_template,
    validate_chapter_title_placeholder,
    validate_filename_template,
)
from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.attribution.base import ATTRIBUTOR_MODES, VOICE_MODES
from book2audio.attribution.gender import validate_gender_overrides
from book2audio.cast_review import validate_character_aliases
from book2audio.tts.voices import (
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    KOKORO_VOICES,
    MALE_VOICES,
    is_known_voice,
)

if TYPE_CHECKING:
    from book2audio.epub.numbering import Numbered


@dataclass
class Config:
    epub_path: str
    output_path: str  # final .m4b
    work_dir: str  # scratch: chapter wavs, cover, state

    narrator_voice: str = DEFAULT_NARRATOR_VOICE
    voice_overrides: dict[str, str] = field(default_factory=dict)
    # Casting bias: prefer this voice for male/female-guessed characters
    # before falling back to the rest of the gender pool.
    male_voice: str | None = None
    female_voice: str | None = None
    speed: float = 1.0
    bitrate: str = "64k"

    # Pauses (seconds) that shape the listening experience.
    paragraph_gap: float = 0.35
    scene_break_gap: float = 1.1
    title_gap: float = 0.8
    chapter_gap: float = 1.8

    # Which chapters to render. Both settings use the book's own chapter
    # numbers ("Chapter 7" is 7), not positions in the file; material before
    # Chapter 1 (synopsis, info pages) is number 0.
    #
    # ``start_chapter``: None = automatic, start at the story's first chapter and
    # skip front matter; 0 = include front matter; N = start at book chapter N.
    # ``chapter_limit``: 0 = through the end of the book; otherwise render that
    # many book numbers from the start, so ``start_chapter=1, chapter_limit=10``
    # is Chapters 1 to 10. A kept prologue (number 0) is extra: it does not use
    # up the limit. Books that do not number their chapters fall back to
    # position numbering (1 = the first section).
    chapter_limit: int = 0
    start_chapter: int | None = None

    # Emit a playable .m4b every ``batch_size`` chapters so listening can start
    # long before the whole book finishes (0 = one file for the whole book).
    batch_size: int = 10

    # Output file names: placeholders {title} {part} {part:0Nd} {chapter}
    # {chapter:0Nd} {chapter_title}; see ``assemble.filenames``. None keeps the
    # legacy naming (``<output>.m4b``, ``<output>-part-NN.m4b``) except for one
    # file per chapter (``batch_size == 1``), which is named
    # "<chapter number> - <chapter title>" by default.
    filename_template: str | None = None
    # Force the legacy output names (<output>.m4b, <output>-part-NN.m4b) and ignore
    # both the template and the one-file-per-chapter default. The hosted worker
    # uses this: its files are internal, and downloads are named by the API.
    legacy_file_names: bool = False

    # Procedural ambient music bed, mixed under the narration.
    music: bool = True
    music_volume: float = 0.22  # bed RMS as a fraction of the narration's RMS
    music_preset: str = "ambient"

    # Punctuation-driven pacing/energy (a coarse substitute for real prosody;
    # Kokoro has no per-word emphasis control).
    expressive: bool = True

    # Per-chapter cover thumbnails via freellmapi. Off by default; enabling it
    # without freellmapi credentials configured fails fast at worker startup.
    thumbnails: bool = False
    # Level 2 thumbnails: a language model reads each chapter first and writes a scene
    # brief for the picture. Only valid with ``thumbnails``; without chat credentials
    # the job quietly stays at the plain template prompt.
    thumbnails_enhanced: bool = False

    # Who speaks each line: the offline heuristic, or an LLM (``llm`` asks about
    # every line, ``hybrid`` only about lines the heuristic had to guess). LLM
    # modes need freellmapi credentials and stop calling after
    # ``attribution_max_calls`` windows, keeping heuristic labels for the rest.
    # That caps windows, not HTTP requests: a retry or a rotation to the next model
    # sends another request for the same window (the run summary reports both).
    attributor: str = "heuristic"
    attribution_max_calls: int = 150

    # ``cast``: each character gets a voice of their own gender; characters whose
    # gender is not clear enough are read by the narrator voice, never a guessed
    # one. ``narrator_only``: the narrator voice reads everything and speaker
    # attribution is skipped.
    voice_mode: str = "cast"
    # Character name -> "m" or "f". The user's word beats every inference.
    gender_overrides: dict[str, str] = field(default_factory=dict)
    # Detected name -> name that takes over its lines (the user's "same person" fixes
    # from the cast review). The target may be the narrator.
    character_aliases: dict[str, str] = field(default_factory=dict)
    # Hosted jobs only: pause once the cast is known so the user can fix it before any
    # audio is made. Ignored for narrator_only, which has no cast. The CLI never pauses.
    cast_review: bool = False

    def validate(self) -> None:
        import os

        if not os.path.isfile(self.epub_path):
            raise ValueError(f"EPUB not found: {self.epub_path}")
        # A cloned voice (``clone:<uuid>``) is also fine: whether it exists, and whose it is,
        # is the API's and the worker's business, not the config's.
        if not is_known_voice(self.narrator_voice):
            raise ValueError(
                f"Unknown narrator voice {self.narrator_voice!r}. "
                f"Known: {', '.join(sorted(KOKORO_VOICES))}"
            )
        if self.male_voice is not None and self.male_voice not in MALE_VOICES:
            raise ValueError(
                f"Unknown male_voice {self.male_voice!r}. "
                f"Known: {', '.join(sorted(MALE_VOICES))}"
            )
        if self.female_voice is not None and self.female_voice not in FEMALE_VOICES:
            raise ValueError(
                f"Unknown female_voice {self.female_voice!r}. "
                f"Known: {', '.join(sorted(FEMALE_VOICES))}"
            )
        if self.music_preset not in MUSIC_PRESETS:
            raise ValueError(
                f"Unknown music_preset {self.music_preset!r}. "
                f"Known: {', '.join(sorted(MUSIC_PRESETS))}"
            )
        if not 0.5 <= self.speed <= 2.0:
            raise ValueError(f"speed must be between 0.5 and 2.0, got {self.speed}")
        if self.attributor not in ATTRIBUTOR_MODES:
            raise ValueError(
                f"Unknown attributor {self.attributor!r}. Known: {', '.join(ATTRIBUTOR_MODES)}"
            )
        if self.attribution_max_calls < 0:
            raise ValueError("attribution_max_calls cannot be negative")
        if self.voice_mode not in VOICE_MODES:
            raise ValueError(
                f"Unknown voice_mode {self.voice_mode!r}. Known: {', '.join(VOICE_MODES)}"
            )
        validate_gender_overrides(self.gender_overrides)
        validate_character_aliases(self.character_aliases)
        if self.thumbnails_enhanced and not self.thumbnails:
            raise ValueError("thumbnails_enhanced requires thumbnails to be on")
        if self.chapter_limit < 0:
            raise ValueError("chapter_limit cannot be negative")
        if self.start_chapter is not None and self.start_chapter < 0:
            raise ValueError("start_chapter cannot be negative")
        if self.batch_size < 0:
            raise ValueError("batch_size cannot be negative")
        if self.filename_template is not None:
            validate_filename_template(self.filename_template)
        validate_chapter_title_placeholder(self.filename_template, self.batch_size)
        if self.chapter_gap < 0:
            raise ValueError("chapter_gap cannot be negative")
        if not 0.0 <= self.music_volume <= 1.0:
            raise ValueError(f"music_volume must be between 0.0 and 1.0, got {self.music_volume}")

    def resolved_filename_template(self) -> str | None:
        """The template that names output files, or None for legacy naming."""
        if self.legacy_file_names:
            return None
        return self.filename_template or default_filename_template(self.batch_size)

    def selection_bounds(self, sections: Sequence[Numbered]) -> tuple[int, int]:
        """0-based ``[start, end)`` slice bounds over ``sections`` (in reading order).

        Single source of truth for combining ``start_chapter`` and
        ``chapter_limit`` — both ``pipeline.py`` and ``worker/render.py`` must
        use this instead of recomputing the same arithmetic independently.
        ``sections`` need ``number`` and ``front_matter`` (parsed or built
        chapters). An empty slice means no chapter matches ``start_chapter``.
        """
        total = len(sections)
        if self.start_chapter is None:
            start_pos = next((i for i, s in enumerate(sections) if not s.front_matter), 0)
        elif self.start_chapter == 0:
            start_pos = 0
        else:
            start_pos = next(
                (i for i, s in enumerate(sections) if s.number >= self.start_chapter), total
            )
        if start_pos >= total or self.chapter_limit == 0:
            return start_pos, total

        # Count from the first selected section's own number so a book with a
        # gap at the requested start still yields chapter_limit chapters. A kept
        # prologue (number 0, not front matter) does not use up the limit, so
        # automatic start plus a limit of 10 is the prologue and Chapters 1 to 10.
        first = sections[start_pos]
        counted_from = first.number if first.front_matter else max(first.number, 1)
        stop_number = counted_from + self.chapter_limit
        end_pos = next(
            (i for i in range(start_pos, total) if sections[i].number >= stop_number), total
        )
        return start_pos, end_pos
