"""Synthetic recordings for the voice-cloning tests."""

from __future__ import annotations

import io

import numpy as np
import soundfile as sf


def speech_like(seconds=15.0, sample_rate=24000, peak=0.5, noise=0.0005, seed=1) -> np.ndarray:
    """Tone bursts with pauses between them, over a quiet noise floor."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    envelope = (np.sin(2 * np.pi * 1.5 * t) > -0.2).astype(np.float64)  # talk, pause, talk
    voice = np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 360 * t)
    signal = peak * envelope * voice / 1.5 + noise * rng.standard_normal(len(t))
    return signal.astype(np.float32)


def wav_bytes(samples: np.ndarray, sample_rate: int = 24000, fmt: str = "WAV") -> bytes:
    out = io.BytesIO()
    sf.write(out, samples, sample_rate, format=fmt)
    return out.getvalue()
