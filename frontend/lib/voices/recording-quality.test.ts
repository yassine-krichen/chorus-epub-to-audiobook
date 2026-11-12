import { describe, expect, it } from "vitest";

import {
  analyzeRecording,
  encodeWav,
  frameLevelsDb,
  mixToMono,
  percentile,
} from "@/lib/voices/recording-quality";

/** Tone bursts with pauses between them over a quiet noise floor, like speech. */
function speechLike(
  seconds: number,
  { sampleRate = 24_000, peak = 0.5, noise = 0.0005 } = {}
): Float32Array {
  const samples = new Float32Array(Math.floor(seconds * sampleRate));
  let seed = 12345;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296 - 0.5;
  };
  for (let i = 0; i < samples.length; i++) {
    const t = i / sampleRate;
    const talking = Math.sin(2 * Math.PI * 1.5 * t) > -0.2 ? 1 : 0;
    const voice = Math.sin(2 * Math.PI * 180 * t) + 0.5 * Math.sin(2 * Math.PI * 360 * t);
    samples[i] = (peak * talking * voice) / 1.5 + noise * 3.4 * random();
  }
  return samples;
}

function failedIds(samples: Float32Array, sampleRate = 24_000): string[] {
  return analyzeRecording(samples, sampleRate)
    .checks.filter((check) => !check.passed)
    .map((check) => check.id);
}

describe("analyzeRecording", () => {
  it("accepts a clear 15 second recording", () => {
    const analysis = analyzeRecording(speechLike(15), 24_000);

    expect(analysis.passed).toBe(true);
    expect(analysis.checks.every((check) => check.advice === "")).toBe(true);
    expect(analysis.durationS).toBeCloseTo(15, 1);
  });

  it("rejects a recording that is too short, with advice", () => {
    const analysis = analyzeRecording(speechLike(5), 24_000);

    expect(failedIds(speechLike(5))).toEqual(["length"]);
    expect(analysis.checks[0].advice).toContain("at least 10");
  });

  it("rejects a recording that is too long", () => {
    expect(failedIds(speechLike(40, { sampleRate: 16_000 }), 16_000)).toEqual(["length"]);
  });

  it("allows half a second of slack over 30 seconds", () => {
    expect(analyzeRecording(speechLike(30.4), 24_000).passed).toBe(true);
  });

  it("rejects a microphone that records below 16 kHz", () => {
    const low = speechLike(15, { sampleRate: 8_000 });

    expect(failedIds(low, 8_000)).toEqual(["quality"]);
  });

  it("rejects a clipped recording", () => {
    const loud = speechLike(15, { peak: 3 }).map((x) => Math.max(-1, Math.min(1, x)));

    expect(failedIds(loud)).toContain("clipping");
  });

  it("rejects a recording that is too quiet, without also calling it noisy", () => {
    const quiet = speechLike(15, { peak: 0.0006, noise: 0.00001 });

    expect(failedIds(quiet)).toEqual(["volume"]);
  });

  it("does not mistake a fluent reading with hardly any pauses for noise", () => {
    const sampleRate = 24_000;
    const fluent = new Float32Array(15 * sampleRate).map((_, i) => {
      const t = i / sampleRate;
      const envelope = 0.01 + 0.29 * (0.5 + 0.5 * Math.sin(2 * Math.PI * 3 * t));
      return envelope * Math.sin(2 * Math.PI * 180 * t);
    });

    expect(failedIds(fluent)).toEqual([]);
  });

  it("rejects background noise", () => {
    expect(failedIds(speechLike(15, { noise: 0.05 }))).toEqual(["noise"]);
  });

  it("rejects silence", () => {
    expect(failedIds(new Float32Array(24_000 * 15))).toContain("volume");
  });

  it("reports every problem at once", () => {
    const bad = speechLike(5, { peak: 3 }).map((x) => Math.max(-1, Math.min(1, x)));

    expect(failedIds(bad)).toEqual(expect.arrayContaining(["length", "clipping"]));
  });
});

describe("helpers", () => {
  it("percentile interpolates like numpy", () => {
    expect(percentile([1, 2, 3, 4], 50)).toBe(2.5);
    expect(percentile([10], 90)).toBe(10);
    expect(percentile([0, 10], 10)).toBeCloseTo(1);
  });

  it("frame levels are in dBFS", () => {
    const half = new Float32Array(24_000).fill(0.5);

    expect(frameLevelsDb(half, 24_000)[0]).toBeCloseTo(-6.02, 1);
  });

  it("mixes stereo down to mono", () => {
    const mono = mixToMono([new Float32Array([1, 0]), new Float32Array([0, 1])]);

    expect(Array.from(mono)).toEqual([0.5, 0.5]);
  });
});

describe("encodeWav", () => {
  it("writes a 16-bit mono PCM WAV with the right header", () => {
    const wav = encodeWav(new Float32Array([0, 0.5, -0.5, 2, -2]), 24_000);
    const view = new DataView(wav.buffer);
    const text = (offset: number, length: number) =>
      String.fromCharCode(...wav.slice(offset, offset + length));

    expect(text(0, 4)).toBe("RIFF");
    expect(text(8, 4)).toBe("WAVE");
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(24_000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(10); // five 16-bit samples
    expect(wav.length).toBe(44 + 10);
    expect(view.getInt16(46, true)).toBe(16384);
    expect(view.getInt16(50, true)).toBe(32767); // clamped, not wrapped
    expect(view.getInt16(52, true)).toBe(-32767);
  });
});
