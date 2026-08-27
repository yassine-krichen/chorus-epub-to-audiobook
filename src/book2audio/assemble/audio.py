"""Low-level audio helpers: silence, gap-joined concatenation, WAV I/O."""

from __future__ import annotations

import numpy as np
import soundfile as sf

from book2audio.tts.engine import SAMPLE_RATE


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def normalize_rms(samples: np.ndarray, target_rms: float = 0.06) -> np.ndarray:
    """Scale ``samples`` so its loudness (RMS) matches ``target_rms``.

    Evens out take-to-take loudness swings from the TTS model so narration and
    dialogue read at a consistent volume, per the "volume consistency" goal —
    a big part of why raw TTS reads as flat/inconsistent. Near-silent clips are
    left alone so we never amplify noise floor into audible hiss.
    """
    if samples.size == 0:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1e-4:
        return samples
    return (samples * (target_rms / rms)).astype(np.float32)


def concat_with_gaps(clips: list[tuple[np.ndarray, float]]) -> np.ndarray:
    """Join ``(samples, trailing_gap_seconds)`` pairs into one waveform.

    The trailing gap is what gives narration its breathing room and marks
    speaker changes and scene breaks with proportionally longer pauses.
    """
    parts: list[np.ndarray] = []
    for samples, gap in clips:
        if samples.size:
            parts.append(samples)
        if gap > 0:
            parts.append(silence(gap))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts)


def write_wav(path: str, samples: np.ndarray) -> None:
    sf.write(path, samples, SAMPLE_RATE, subtype="PCM_16")


def duration_seconds(path: str) -> float:
    info = sf.info(path)
    return info.frames / info.samplerate
