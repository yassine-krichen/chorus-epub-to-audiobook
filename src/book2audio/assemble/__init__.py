"""Assembly: segments -> chapter WAVs -> a single tagged .m4b audiobook."""

from book2audio.assemble.audio import concat_with_gaps, normalize_rms, silence, write_wav
from book2audio.assemble.audiobook import build_m4b
from book2audio.assemble.music import generate_ambient_bed, mix_music_bed

__all__ = [
    "write_wav",
    "silence",
    "concat_with_gaps",
    "normalize_rms",
    "build_m4b",
    "generate_ambient_bed",
    "mix_music_bed",
]
