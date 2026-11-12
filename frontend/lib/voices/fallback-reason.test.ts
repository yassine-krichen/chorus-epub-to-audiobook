import { describe, expect, it } from "vitest";

import {
  KNOWN_FALLBACK_REASONS,
  fallbackReasonText,
  fallbackVoiceText,
} from "@/lib/voices/fallback-reason";

describe("fallbackReasonText", () => {
  it("shows each fixed reason as it is", () => {
    for (const reason of KNOWN_FALLBACK_REASONS) {
      expect(fallbackReasonText(reason)).toBe(reason);
    }
  });

  it("never shows a raw engine message, path or stack trace", () => {
    const raw =
      "oom: CUDA out of memory C:/Users/someone/venv/torch/cuda.py token=sk-123 " + "x".repeat(500);

    const shown = fallbackReasonText(raw);

    expect(shown).toBe("the voice-cloning engine could not be used");
    expect(shown).not.toContain("someone");
    expect(shown).not.toContain("sk-123");
  });

  it("treats an empty reason as unknown", () => {
    expect(fallbackReasonText("")).toBe("the voice-cloning engine could not be used");
  });
});

describe("fallbackVoiceText", () => {
  it("shows a built-in voice id and hides anything else", () => {
    expect(fallbackVoiceText("am_michael")).toBe("am_michael");
    expect(fallbackVoiceText("C:/Users/someone/x")).toBe("a built-in voice");
    expect(fallbackVoiceText("")).toBe("a built-in voice");
  });
});
