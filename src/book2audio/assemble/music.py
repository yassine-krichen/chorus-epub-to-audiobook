"""Procedural warm ambient pad — a royalty-free, generated background bed.

No external audio assets: everything is synthesized from plain sine partials
over a low, open chord (root + fifth + ninth, kept low-mid so it sits under
speech rather than competing with it), each with its own slow amplitude LFO
for a "breathing" pad feel, plus a touch of filtered noise for air. Partial
frequencies are snapped to an exact integer number of cycles over the loop
length so the generated clip tiles with zero click at the seam.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from book2audio.tts.engine import SAMPLE_RATE

LOOP_SECONDS = 32.0

# (root_hz, amplitude) — a warm, open add9-ish chord, low-mid register so it
# sits under narration without masking it.
_CHORD = (
    (55.0, 0.35),  # sub root, warmth
    (110.0, 0.30),  # root
    (164.81, 0.20),  # perfect fifth
    (246.94, 0.15),  # major ninth, adds shimmer without dissonance
)

# A lower, sparser voicing than _CHORD — fewer partials, longer LFO periods,
# for a still, held drone rather than a "breathing" pad.
_DRONE_CHORD = (
    (36.71, 0.40),  # low D, sub warmth
    (73.42, 0.30),  # octave up
    (110.0, 0.15),  # perfect fifth above the octave
)

_DETUNE_CENTS = 4.0  # per partial, for a soft chorus width


def _snap_to_loop(freq_hz: float) -> float:
    """Nearest frequency to ``freq_hz`` that completes a whole number of
    cycles over ``LOOP_SECONDS`` — i.e. perfectly periodic at the loop length."""
    return round(freq_hz * LOOP_SECONDS) / LOOP_SECONDS


def _generate_chord_bed(
    chord: tuple[tuple[float, float], ...], seed: int, noise_amount: float = 0.04
) -> np.ndarray:
    """Shared synthesis: sine partials over ``chord`` plus filtered noise air.

    Factored out of ``generate_ambient_bed`` so other presets (drone, etc.)
    can reuse the exact same loop-safe technique with a different voicing.
    """
    n = int(LOOP_SECONDS * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    rng = np.random.default_rng(seed)

    bed = np.zeros(n, dtype=np.float64)
    for root_hz, amp in chord:
        # Snap every partial — root AND its detuned companion — to an exact
        # integer number of cycles over the loop, so each is perfectly
        # periodic at LOOP_SECONDS and the pair tiles with no seam click.
        # Loop resolution (1/32 Hz) is far finer than a few cents of detune,
        # so the detune amount survives the snap essentially unchanged.
        freq = _snap_to_loop(root_hz)
        detuned = _snap_to_loop(freq * 2 ** (_DETUNE_CENTS / 1200))

        lfo_cycles = max(1, round(rng.uniform(2, 5)))  # slow, still loop-safe
        lfo_freq = lfo_cycles / LOOP_SECONDS
        lfo_phase = rng.uniform(0, 2 * np.pi)
        lfo = 0.7 + 0.3 * np.sin(2 * np.pi * lfo_freq * t + lfo_phase)

        tone = np.sin(2 * np.pi * freq * t) + np.sin(2 * np.pi * detuned * t)
        bed += amp * 0.5 * tone * lfo

    # Soft filtered noise for air: a moving-average turns white noise into a
    # gentle shimmer instead of harsh hiss. Filtered *circularly* (via FFT)
    # rather than linearly — a linear filter leaves the last ~64 samples
    # unrelated to the first ~64, which clicks at the loop seam; a circular
    # filter is periodic by construction, so it tiles as cleanly as the tones.
    noise = rng.standard_normal(n)
    kernel = np.zeros(n)
    kernel[:64] = 1.0 / 64
    air = np.fft.irfft(np.fft.rfft(noise) * np.fft.rfft(kernel), n=n)
    bed += noise_amount * air

    peak = np.max(np.abs(bed)) or 1.0
    return (bed / peak * 0.7).astype(np.float32)


def generate_ambient_bed(seed: int = 0) -> np.ndarray:
    """Return one seamlessly loopable ``LOOP_SECONDS`` clip, peak-safe."""
    return _generate_chord_bed(_CHORD, seed)


def generate_drone_bed(seed: int = 0) -> np.ndarray:
    """A lower, stiller bed than the default ambient pad — fewer partials,
    slower movement, less noise air, for a held-drone feel."""
    return _generate_chord_bed(_DRONE_CHORD, seed, noise_amount=0.02)


def generate_rain_bed(seed: int = 0) -> np.ndarray:
    """Filtered broadband noise shaped into a rain/hiss texture with slow
    "gusts", using the same circular-FFT-filter and loop-safe-LFO techniques
    as the tonal presets above."""
    n = int(LOOP_SECONDS * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    rng = np.random.default_rng(seed)

    # Tighter kernel than the ambient bed's "air" filter -> more high-frequency
    # content survives, giving a hiss texture instead of a soft shimmer.
    noise = rng.standard_normal(n)
    kernel = np.zeros(n)
    kernel[:8] = 1.0 / 8
    rain = np.fft.irfft(np.fft.rfft(noise) * np.fft.rfft(kernel), n=n)

    # Slow "gusts": an integer-cycle LFO over the loop length keeps the
    # amplitude swell loop-safe, same trick as the tonal presets' pad LFOs.
    gust_cycles = max(1, round(rng.uniform(2, 4)))
    gust_freq = gust_cycles / LOOP_SECONDS
    gust_phase = rng.uniform(0, 2 * np.pi)
    gust = 0.75 + 0.25 * np.sin(2 * np.pi * gust_freq * t + gust_phase)
    rain = rain * gust

    peak = np.max(np.abs(rain)) or 1.0
    return (rain / peak * 0.7).astype(np.float32)


MUSIC_PRESETS: dict[str, Callable[[int], np.ndarray]] = {
    "ambient": generate_ambient_bed,
    "drone": generate_drone_bed,
    "rain": generate_rain_bed,
}


def generate_bed_for_preset(preset: str, seed: int = 0) -> np.ndarray:
    """Dispatch to the named preset's generator. Raises on an unknown name."""
    try:
        generator = MUSIC_PRESETS[preset]
    except KeyError:
        raise ValueError(
            f"Unknown music_preset {preset!r}. Known: {', '.join(sorted(MUSIC_PRESETS))}"
        ) from None
    return generator(seed)


def mix_music_bed(
    narration: np.ndarray,
    bed_loop: np.ndarray,
    *,
    volume: float,
    fade_seconds: float = 2.0,
    phase_offset_seconds: float = 0.0,
) -> np.ndarray:
    """Mix a looping ambient bed under ``narration`` at a relative volume.

    ``volume`` is the bed's target RMS as a fraction of the narration's
    *voiced* RMS (silence stretches are excluded from that measurement so a
    chapter with long pauses doesn't get a falsely quiet bed). The bed fades
    in/out at the clip edges and the final mix is safety-limited so it never
    clips, regardless of how loud the source narration happens to be.
    """
    if narration.size == 0:
        return narration

    bed = _tile_to_length(bed_loop, narration.size, phase_offset_seconds)

    voiced = narration[np.abs(narration) > 1e-4]
    narration_rms = float(np.sqrt(np.mean(np.square(voiced)))) if voiced.size else 0.05
    bed_rms = float(np.sqrt(np.mean(np.square(bed)))) or 1e-6
    bed = bed * (narration_rms * volume / bed_rms)

    fade_n = min(int(fade_seconds * SAMPLE_RATE), narration.size // 2)
    if fade_n > 0:
        envelope = np.ones(narration.size, dtype=np.float32)
        envelope[:fade_n] = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        envelope[-fade_n:] = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
        bed = bed * envelope

    mix = narration + bed
    peak = np.max(np.abs(mix))
    if peak > 0.98:
        mix = mix * (0.98 / peak)  # soft safety limiter, never clips
    return mix.astype(np.float32)


def _tile_to_length(loop: np.ndarray, length: int, phase_offset_seconds: float) -> np.ndarray:
    offset = int(phase_offset_seconds * SAMPLE_RATE) % loop.size
    rolled = np.roll(loop, -offset)
    reps = length // rolled.size + 1
    return np.tile(rolled, reps)[:length]
