/**
 * Quality checks for a voice-cloning recording, run in the browser before upload.
 *
 * The thresholds mirror `book2audio/api/voices/reference_audio.py`, which applies the same
 * rules again on the server (the server never trusts this check). Change both together.
 */

export const MIN_DURATION_S = 10;
/** "20 to 30 seconds" in the UI; half a second of slack for the recorder's stop latency. */
export const MAX_DURATION_S = 30.5;
export const MIN_SAMPLE_RATE = 16_000;
export const CLIPPING_LEVEL = 0.999;
export const MAX_CLIPPED_FRACTION = 0.005;
export const MIN_SPEECH_DB = -35;
export const MAX_NOISE_FLOOR_DB = -32;
export const MIN_SNR_DB = 12;
const FRAME_SECONDS = 0.05;

export type CheckId = "length" | "quality" | "clipping" | "volume" | "noise";

export interface RecordingCheck {
  id: CheckId;
  /** Short name for the checklist, e.g. "Not too loud". */
  label: string;
  passed: boolean;
  /** What to do about it; empty when the check passed. */
  advice: string;
}

export interface RecordingAnalysis {
  checks: RecordingCheck[];
  passed: boolean;
  durationS: number;
  sampleRate: number;
  speechDb: number;
  noiseDb: number;
  clippedFraction: number;
}

/** Linear-interpolated percentile of unsorted values (same as numpy's default). */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return Number.NEGATIVE_INFINITY;
  const sorted = [...values].sort((a, b) => a - b);
  const position = ((sorted.length - 1) * p) / 100;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

/** RMS level in dBFS of each non-overlapping 50 ms frame. */
export function frameLevelsDb(samples: Float32Array, sampleRate: number): number[] {
  const frame = Math.max(1, Math.floor(sampleRate * FRAME_SECONDS));
  const count = Math.floor(samples.length / frame);
  if (count === 0) return [-120];
  const levels: number[] = [];
  for (let f = 0; f < count; f++) {
    let sum = 0;
    for (let i = f * frame; i < (f + 1) * frame; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / frame);
    levels.push(20 * Math.log10(Math.max(rms, 1e-8)));
  }
  return levels;
}

export function clippedFraction(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let clipped = 0;
  for (const sample of samples) if (Math.abs(sample) >= CLIPPING_LEVEL) clipped++;
  return clipped / samples.length;
}

/** Runs every check on a mono recording; `passed` is true only when all of them are. */
export function analyzeRecording(
  samples: Float32Array,
  sampleRate: number
): RecordingAnalysis {
  const durationS = samples.length / sampleRate;
  const clipped = clippedFraction(samples);
  const levels = frameLevelsDb(samples, sampleRate);
  const speechDb = percentile(levels, 90);
  const noiseDb = percentile(levels, 10);

  const tooShort = durationS < MIN_DURATION_S;
  const tooLong = durationS > MAX_DURATION_S;
  const tooQuiet = speechDb < MIN_SPEECH_DB;
  // A recording that is too quiet is reported as that, not also as noisy.
  const noisy = !tooQuiet && (noiseDb > MAX_NOISE_FLOOR_DB || speechDb - noiseDb < MIN_SNR_DB);

  const checks: RecordingCheck[] = [
    {
      id: "length",
      label: "Between 10 and 30 seconds",
      passed: !tooShort && !tooLong,
      advice: tooShort
        ? `That was ${Math.round(durationS)} seconds. Record at least 10, ideally 20 to 30.`
        : tooLong
          ? `That was ${Math.round(durationS)} seconds. Keep it under 30.`
          : "",
    },
    {
      id: "quality",
      label: "Clear audio quality",
      passed: sampleRate >= MIN_SAMPLE_RATE,
      advice:
        sampleRate >= MIN_SAMPLE_RATE
          ? ""
          : "Your microphone records at too low a quality. Try another microphone.",
    },
    {
      id: "clipping",
      label: "Not too loud",
      passed: clipped <= MAX_CLIPPED_FRACTION,
      advice:
        clipped <= MAX_CLIPPED_FRACTION
          ? ""
          : "The sound is distorted (too loud). Move back from the microphone.",
    },
    {
      id: "volume",
      label: "Loud enough",
      passed: !tooQuiet,
      advice: tooQuiet ? "The recording is too quiet. Move closer to the microphone." : "",
    },
    {
      id: "noise",
      label: "Quiet room",
      passed: !noisy,
      advice: noisy ? "There is too much background noise. Find a quieter room." : "",
    },
  ];

  return {
    checks,
    passed: checks.every((check) => check.passed),
    durationS,
    sampleRate,
    speechDb,
    noiseDb,
    clippedFraction: clipped,
  };
}

/** 16-bit mono PCM WAV, the only format the server accepts. */
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(new ArrayBuffer(44 + samples.length * 2));
  const view = new DataView(bytes.buffer);
  const writeText = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(clamped * 32767), true);
  }
  return bytes;
}

/** Averages the channels of a decoded recording into one. */
export function mixToMono(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];
  const mono = new Float32Array(channels[0].length);
  for (const channel of channels) {
    for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / channels.length;
  }
  return mono;
}
