"""Server-side quality checks for a cloning reference recording."""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf

from audio_helpers import speech_like, wav_bytes
from book2audio.api.voices.reference_audio import (
    MAX_UPLOAD_BYTES,
    ReferenceAudioError,
    analyze_reference_upload,
    quality_problems,
)


def test_a_good_recording_is_accepted_and_normalized_to_mono_16_bit_wav():
    analysis = analyze_reference_upload(wav_bytes(speech_like(15)))

    info = sf.info(io.BytesIO(analysis.wav_bytes))
    assert (info.format, info.subtype, info.channels) == ("WAV", "PCM_16", 1)
    assert analysis.sample_rate == 24000
    assert analysis.duration_s == pytest.approx(15.0, abs=0.1)
    assert len(analysis.sha256) == 64


def test_a_stereo_recording_is_mixed_down_to_mono():
    mono = speech_like(15)
    analysis = analyze_reference_upload(wav_bytes(np.stack([mono, mono], axis=1)))

    assert sf.info(io.BytesIO(analysis.wav_bytes)).channels == 1


def test_the_same_recording_always_gets_the_same_hash():
    data = wav_bytes(speech_like(15))

    assert analyze_reference_upload(data).sha256 == analyze_reference_upload(data).sha256


@pytest.mark.parametrize(
    "samples, sample_rate, fragment",
    [
        (speech_like(5), 24000, "too short"),
        (speech_like(40), 16000, "too long"),
        (speech_like(15, peak=0.0006, noise=0.00001), 24000, "too quiet"),
        (np.clip(speech_like(15, peak=3.0), -1, 1), 24000, "clipped"),
        (speech_like(15, noise=0.05), 24000, "background noise"),
    ],
)
def test_a_recording_that_breaks_a_quality_rule_is_refused_with_the_reason(
    samples, sample_rate, fragment
):
    with pytest.raises(ReferenceAudioError, match=fragment):
        analyze_reference_upload(wav_bytes(samples, sample_rate))


def test_a_low_sample_rate_is_refused():
    with pytest.raises(ReferenceAudioError, match="16 kHz"):
        analyze_reference_upload(wav_bytes(speech_like(15, sample_rate=8000), 8000))


def test_a_file_that_is_not_audio_is_refused():
    with pytest.raises(ReferenceAudioError, match="could not be read"):
        analyze_reference_upload(b"not audio at all" * 100)


def test_only_wav_is_accepted_even_when_the_audio_itself_is_fine():
    with pytest.raises(ReferenceAudioError, match="WAV"):
        analyze_reference_upload(wav_bytes(speech_like(15), fmt="FLAC"))


def test_an_oversized_upload_is_refused_before_it_is_decoded():
    with pytest.raises(ReferenceAudioError, match="too large"):
        analyze_reference_upload(b"\0" * (MAX_UPLOAD_BYTES + 1))


def test_silence_is_refused():
    with pytest.raises(ReferenceAudioError, match="too quiet"):
        analyze_reference_upload(wav_bytes(np.zeros(24000 * 15, dtype=np.float32)))


def test_quality_problems_lists_every_problem_at_once():
    problems = quality_problems(np.clip(speech_like(5, peak=3.0), -1, 1), 24000)

    assert any("short" in p for p in problems)
    assert any("clipped" in p for p in problems)
