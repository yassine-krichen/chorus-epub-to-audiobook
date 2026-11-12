"""Quality checks for a voice-cloning reference recording, and its normalized form.

The browser checks a recording before uploading it, but the server never trusts that: it
decodes the upload itself and applies the same rules. The thresholds here are mirrored in
``frontend/lib/voices/recording-quality.ts``; change both together.

Only WAV is accepted. The browser decodes whatever the microphone recorded (webm, mp4) and
uploads 16-bit mono PCM, so the server needs no ffmpeg and no new dependency: ``soundfile``
is already installed.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MIN_DURATION_S = 10.0
# "20 to 30 seconds" in the UI; half a second of slack for the browser's stop latency.
MAX_DURATION_S = 30.5
MIN_SAMPLE_RATE = 16_000
MAX_SAMPLE_RATE = 48_000
CLIPPING_LEVEL = 0.999
MAX_CLIPPED_FRACTION = 0.005
MIN_SPEECH_DB = -35.0
MAX_NOISE_FLOOR_DB = -40.0
MIN_SNR_DB = 15.0
_FRAME_SECONDS = 0.05


class ReferenceAudioError(ValueError):
    """The upload is not a usable reference recording; the message is safe to show the user."""


@dataclass(frozen=True)
class ReferenceAnalysis:
    wav_bytes: bytes  # normalized: mono, 16-bit PCM WAV
    sha256: str
    duration_s: float
    sample_rate: int


def frame_levels_db(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """RMS level in dBFS of each non-overlapping 50 ms frame."""
    frame = max(1, int(sample_rate * _FRAME_SECONDS))
    count = len(samples) // frame
    if count == 0:
        return np.full(1, -120.0)
    frames = samples[: count * frame].reshape(count, frame).astype(np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-8))


def quality_problems(samples: np.ndarray, sample_rate: int) -> list[str]:
    """Plain-language problems with a mono recording; empty when it is good enough."""
    problems: list[str] = []
    duration = len(samples) / sample_rate
    if duration < MIN_DURATION_S:
        problems.append(f"The recording is too short ({duration:.0f} s). Record at least 10 seconds.")
    if duration > MAX_DURATION_S:
        problems.append(f"The recording is too long ({duration:.0f} s). Keep it under 30 seconds.")
    if sample_rate < MIN_SAMPLE_RATE:
        problems.append("The recording's audio quality is too low (it needs at least 16 kHz).")
    if float(np.mean(np.abs(samples) >= CLIPPING_LEVEL)) > MAX_CLIPPED_FRACTION:
        problems.append("The recording is clipped (too loud). Move back from the microphone.")
    levels = frame_levels_db(samples, sample_rate)
    speech_db = float(np.percentile(levels, 90))
    noise_db = float(np.percentile(levels, 10))
    if speech_db < MIN_SPEECH_DB:
        problems.append("The recording is too quiet. Move closer to the microphone.")
    elif noise_db > MAX_NOISE_FLOOR_DB or speech_db - noise_db < MIN_SNR_DB:
        problems.append("There is too much background noise. Find a quieter room.")
    return problems


def analyze_reference_upload(data: bytes) -> ReferenceAnalysis:
    """Decode an uploaded WAV, apply the quality rules and return its normalized form.

    Raises :class:`ReferenceAudioError` for anything that is not a usable recording.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise ReferenceAudioError("The recording file is too large.")
    try:
        info = sf.info(io.BytesIO(data))
        if info.format != "WAV" or info.channels > 2:
            raise ReferenceAudioError("The recording must be a WAV file (mono or stereo).")
        if not MIN_SAMPLE_RATE <= info.samplerate <= MAX_SAMPLE_RATE:
            if info.samplerate < MIN_SAMPLE_RATE:
                raise ReferenceAudioError(
                    "The recording's audio quality is too low (it needs at least 16 kHz)."
                )
            raise ReferenceAudioError("The recording's sample rate is not supported.")
        # Checked from the header, before decoding: a tiny file can claim hours of audio.
        if info.frames / info.samplerate > MAX_DURATION_S + 5:
            raise ReferenceAudioError("The recording is too long. Keep it under 30 seconds.")
        samples, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except ReferenceAudioError:
        raise
    except (RuntimeError, ValueError, OSError):
        raise ReferenceAudioError("The recording could not be read as audio.") from None

    mono = samples.mean(axis=1)
    if not np.isfinite(mono).all():
        raise ReferenceAudioError("The recording could not be read as audio.")
    problems = quality_problems(mono, sample_rate)
    if problems:
        raise ReferenceAudioError(" ".join(problems))

    out = io.BytesIO()
    sf.write(out, mono, sample_rate, format="WAV", subtype="PCM_16")
    wav_bytes = out.getvalue()
    return ReferenceAnalysis(
        wav_bytes=wav_bytes,
        sha256=hashlib.sha256(wav_bytes).hexdigest(),
        duration_s=round(len(mono) / sample_rate, 2),
        sample_rate=sample_rate,
    )
