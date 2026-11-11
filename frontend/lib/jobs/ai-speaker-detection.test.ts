import { describe, expect, it } from "vitest";

import {
  attributorForSwitch,
  isAiSpeakerDetection,
  voiceModePatch,
} from "@/lib/jobs/ai-speaker-detection";

describe("attributorForSwitch", () => {
  it("maps on to hybrid and off to heuristic", () => {
    expect(attributorForSwitch(true)).toBe("hybrid");
    expect(attributorForSwitch(false)).toBe("heuristic");
  });
});

describe("isAiSpeakerDetection", () => {
  it("is true only for an attributor that asks the AI model", () => {
    expect(isAiSpeakerDetection("hybrid")).toBe(true);
    expect(isAiSpeakerDetection("heuristic")).toBe(false);
  });
});

describe("voiceModePatch", () => {
  it("turning narrator only on also switches AI speaker detection off", () => {
    expect(voiceModePatch(true)).toEqual({ voice_mode: "narrator_only", attributor: "heuristic" });
  });

  it("turning narrator only off changes only the voice mode", () => {
    expect(voiceModePatch(false)).toEqual({ voice_mode: "cast" });
  });
});
