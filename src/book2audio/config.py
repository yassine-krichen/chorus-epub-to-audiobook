"""Generation settings, validated at startup.

Per our security posture, config is validated the moment it's built — a bad
voice id or nonsensical speed fails immediately with a clear message, not
halfway through a 16-hour render.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from book2audio.tts.voices import CHARACTER_VOICE_POOL, DEFAULT_NARRATOR_VOICE

# Every voice the engine can use, for validating overrides up front.
_KNOWN_VOICES = frozenset(CHARACTER_VOICE_POOL) | {DEFAULT_NARRATOR_VOICE}


@dataclass
class Config:
    epub_path: str
    output_path: str  # final .m4b
    work_dir: str  # scratch: chapter wavs, cover, state

    narrator_voice: str = DEFAULT_NARRATOR_VOICE
    voice_overrides: dict[str, str] = field(default_factory=dict)
    speed: float = 1.0
    bitrate: str = "64k"

    # Pauses (seconds) that shape the listening experience.
    paragraph_gap: float = 0.35
    scene_break_gap: float = 1.1
    title_gap: float = 0.8
    chapter_gap: float = 1.8

    # Optional cap on chapters, for quick previews (0 = whole book).
    chapter_limit: int = 0

    # Emit a playable .m4b every ``batch_size`` chapters so listening can start
    # long before the whole book finishes (0 = one file for the whole book).
    batch_size: int = 10

    # Procedural ambient music bed, mixed under the narration.
    music: bool = True
    music_volume: float = 0.22  # bed RMS as a fraction of the narration's RMS

    # Punctuation-driven pacing/energy (a coarse substitute for real prosody;
    # Kokoro has no per-word emphasis control).
    expressive: bool = True

    def validate(self) -> None:
        import os

        if not os.path.isfile(self.epub_path):
            raise ValueError(f"EPUB not found: {self.epub_path}")
        if self.narrator_voice not in _KNOWN_VOICES:
            raise ValueError(
                f"Unknown narrator voice {self.narrator_voice!r}. "
                f"Known: {', '.join(sorted(_KNOWN_VOICES))}"
            )
        if not 0.5 <= self.speed <= 2.0:
            raise ValueError(f"speed must be between 0.5 and 2.0, got {self.speed}")
        if self.chapter_limit < 0:
            raise ValueError("chapter_limit cannot be negative")
        if self.batch_size < 0:
            raise ValueError("batch_size cannot be negative")
        if self.chapter_gap < 0:
            raise ValueError("chapter_gap cannot be negative")
        if not 0.0 <= self.music_volume <= 1.0:
            raise ValueError(f"music_volume must be between 0.0 and 1.0, got {self.music_volume}")
