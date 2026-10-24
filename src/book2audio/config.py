"""Generation settings, validated at startup.

Per our security posture, config is validated the moment it's built — a bad
voice id or nonsensical speed fails immediately with a clear message, not
halfway through a 16-hour render.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from book2audio.assemble.music import MUSIC_PRESETS
from book2audio.attribution.base import ATTRIBUTOR_MODES
from book2audio.tts.voices import (
    CHARACTER_VOICE_POOL,
    DEFAULT_NARRATOR_VOICE,
    FEMALE_VOICES,
    MALE_VOICES,
)

# Every voice the engine can use, for validating overrides up front.
_KNOWN_VOICES = frozenset(CHARACTER_VOICE_POOL) | {DEFAULT_NARRATOR_VOICE}


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

    # Optional cap on chapters, for quick previews (0 = whole book).
    chapter_limit: int = 0

    # 1-based chapter to start rendering from (matches ``Chapter.index``'s
    # own 1-based convention), for resuming partway through a book or
    # skipping front matter. ``chapter_limit`` counts from here, not from the
    # book's beginning.
    start_chapter: int = 1

    # Emit a playable .m4b every ``batch_size`` chapters so listening can start
    # long before the whole book finishes (0 = one file for the whole book).
    batch_size: int = 10

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

    # Who speaks each line: the offline heuristic, or an LLM (``llm`` asks about
    # every line, ``hybrid`` only about lines the heuristic had to guess). LLM
    # modes need freellmapi credentials and stop calling after
    # ``attribution_max_calls`` windows (a retried request is not counted again),
    # keeping heuristic labels for the rest.
    attributor: str = "heuristic"
    attribution_max_calls: int = 150

    def validate(self) -> None:
        import os

        if not os.path.isfile(self.epub_path):
            raise ValueError(f"EPUB not found: {self.epub_path}")
        if self.narrator_voice not in _KNOWN_VOICES:
            raise ValueError(
                f"Unknown narrator voice {self.narrator_voice!r}. "
                f"Known: {', '.join(sorted(_KNOWN_VOICES))}"
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
        if self.chapter_limit < 0:
            raise ValueError("chapter_limit cannot be negative")
        if self.start_chapter < 1:
            raise ValueError("start_chapter must be >= 1")
        if self.batch_size < 0:
            raise ValueError("batch_size cannot be negative")
        if self.chapter_gap < 0:
            raise ValueError("chapter_gap cannot be negative")
        if not 0.0 <= self.music_volume <= 1.0:
            raise ValueError(f"music_volume must be between 0.0 and 1.0, got {self.music_volume}")

    def selection_bounds(self, total: int) -> tuple[int, int]:
        """0-based ``[start, end)`` slice bounds for a chapter list of length ``total``.

        Single source of truth for combining ``start_chapter`` and
        ``chapter_limit`` — both ``pipeline.py`` and ``worker/render.py`` must
        use this instead of recomputing the same arithmetic independently.
        """
        start_pos = max(0, self.start_chapter - 1)
        end_pos = start_pos + self.chapter_limit if self.chapter_limit > 0 else total
        return start_pos, min(end_pos, total)
