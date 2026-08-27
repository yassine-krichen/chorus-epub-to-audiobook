import numpy as np
import pytest

from book2audio.assemble.audio import normalize_rms


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def test_normalizes_to_target_rms():
    rng = np.random.default_rng(0)
    samples = (rng.standard_normal(24_000) * 0.3).astype(np.float32)
    out = normalize_rms(samples, target_rms=0.06)
    assert _rms(out) == pytest.approx(0.06, rel=1e-3)


def test_near_silent_clip_is_left_alone():
    samples = np.zeros(1000, dtype=np.float32)
    samples[500] = 1e-5
    out = normalize_rms(samples, target_rms=0.06)
    assert np.array_equal(out, samples)


def test_empty_array_is_returned_unchanged():
    samples = np.zeros(0, dtype=np.float32)
    out = normalize_rms(samples)
    assert out.size == 0


def test_quiet_clip_is_boosted_up_to_target():
    rng = np.random.default_rng(1)
    quiet = (rng.standard_normal(24_000) * 0.01).astype(np.float32)
    out = normalize_rms(quiet, target_rms=0.06)
    assert _rms(out) > _rms(quiet)
