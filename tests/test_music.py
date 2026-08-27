import numpy as np

from book2audio.assemble.music import LOOP_SECONDS, generate_ambient_bed, mix_music_bed
from book2audio.tts.engine import SAMPLE_RATE


def _narration(seconds: float, amplitude: float = 0.3, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * amplitude).astype(np.float32)


def test_bed_has_the_expected_loop_length():
    bed = generate_ambient_bed()
    assert bed.shape[0] == int(LOOP_SECONDS * SAMPLE_RATE)


def test_bed_never_clips():
    bed = generate_ambient_bed()
    assert np.max(np.abs(bed)) <= 1.0


def test_bed_loop_boundary_is_seamless():
    """No click at the seam: the wrap-around discontinuity must be tiny."""
    bed = generate_ambient_bed()
    wrap_step = abs(float(bed[0]) - float(bed[-1]))
    typical_step = float(np.mean(np.abs(np.diff(bed[:1000]))))
    assert wrap_step < max(typical_step * 20, 0.02)


def test_bed_generation_is_deterministic_for_a_given_seed():
    assert np.array_equal(generate_ambient_bed(seed=1), generate_ambient_bed(seed=1))


def test_mix_never_clips_even_with_loud_narration():
    narration = _narration(5.0, amplitude=0.9)
    bed = generate_ambient_bed()
    mix = mix_music_bed(narration, bed, volume=0.5)
    assert np.max(np.abs(mix)) <= 0.981


def test_mix_preserves_narration_length():
    narration = _narration(3.0)
    bed = generate_ambient_bed()
    mix = mix_music_bed(narration, bed, volume=0.12)
    assert mix.shape[0] == narration.shape[0]


def test_mix_of_empty_narration_is_empty():
    bed = generate_ambient_bed()
    mix = mix_music_bed(np.zeros(0, dtype=np.float32), bed, volume=0.12)
    assert mix.size == 0


def test_higher_volume_yields_louder_mix_than_lower_volume():
    narration = _narration(4.0, amplitude=0.2)
    bed = generate_ambient_bed()
    quiet_mix = mix_music_bed(narration, bed, volume=0.05, fade_seconds=0.0)
    loud_mix = mix_music_bed(narration, bed, volume=0.4, fade_seconds=0.0)
    # Compare the added energy relative to dry narration, not raw levels.
    quiet_added = np.mean(np.square(quiet_mix - narration))
    loud_added = np.mean(np.square(loud_mix - narration))
    assert loud_added > quiet_added


def test_tiling_handles_narration_shorter_than_the_loop():
    narration = _narration(2.0)  # well under LOOP_SECONDS
    bed = generate_ambient_bed()
    mix = mix_music_bed(narration, bed, volume=0.1)
    assert mix.shape[0] == narration.shape[0]
