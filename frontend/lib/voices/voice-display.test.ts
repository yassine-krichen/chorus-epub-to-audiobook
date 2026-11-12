import { describe, expect, it } from "vitest";

import {
  builtInVoices,
  cloneProblemForConfig,
  cloneVoiceIdsInConfig,
  clonedVoices,
  isSelectable,
  labelForVoiceId,
  voiceLabel,
} from "@/lib/voices/voice-display";
import type { VoiceInfo } from "@/types/voice";

const CLONE = "clone:1234abcd-1234-1234-1234-1234567890ab";

const kokoro: VoiceInfo = {
  id: "am_adam",
  gender: "male",
  is_default_narrator: false,
  sample_url: null,
};
const mine = (available: boolean): VoiceInfo => ({
  id: CLONE,
  gender: "female",
  is_default_narrator: false,
  sample_url: null,
  provider: "clone",
  name: "My voice",
  available,
});

describe("voice labels", () => {
  it("names a cloned voice by what the user called it and a built-in one by its id", () => {
    expect(voiceLabel(mine(true))).toBe("My voice");
    expect(voiceLabel(kokoro)).toBe("am_adam");
  });

  it("falls back to a neutral label for a cloned voice that no longer exists", () => {
    expect(labelForVoiceId([kokoro], CLONE)).toBe("A deleted voice");
    expect(labelForVoiceId([kokoro, mine(true)], CLONE)).toBe("My voice");
    expect(labelForVoiceId([kokoro], "af_heart")).toBe("af_heart");
  });

  it("splits built-in voices from cloned ones", () => {
    const all = [kokoro, mine(true)];

    expect(builtInVoices(all)).toEqual([kokoro]);
    expect(clonedVoices(all)).toEqual([mine(true)]);
  });
});

describe("availability", () => {
  it("a cloned voice is selectable only while a capable worker is online", () => {
    expect(isSelectable(mine(true))).toBe(true);
    expect(isSelectable(mine(false))).toBe(false);
    expect(isSelectable(kokoro)).toBe(true); // built-in voices never say otherwise
  });
});

describe("cloneVoiceIdsInConfig", () => {
  it("finds cloned voices in the narrator and in character voices, once each", () => {
    const ids = cloneVoiceIdsInConfig({
      narrator_voice: CLONE,
      voice_overrides: { Sunny: CLONE, Nephis: "af_heart" },
    });

    expect(ids).toEqual([CLONE]);
  });

  it("finds none when only built-in voices are used", () => {
    expect(
      cloneVoiceIdsInConfig({ narrator_voice: null, voice_overrides: { A: "am_adam" } })
    ).toEqual([]);
  });
});

describe("cloneProblemForConfig", () => {
  const config = { narrator_voice: CLONE, voice_overrides: {} };

  it("has no problem when nothing cloned is used", () => {
    expect(
      cloneProblemForConfig({ narrator_voice: null, voice_overrides: {} }, [mine(false)])
    ).toBeNull();
  });

  it("has no problem when the worker is online", () => {
    expect(cloneProblemForConfig(config, [mine(true)])).toBeNull();
  });

  it("says the local worker is needed when it is offline", () => {
    expect(cloneProblemForConfig(config, [mine(false)])).toContain("local worker");
  });

  it("says the voice is gone when it is not in the list", () => {
    expect(cloneProblemForConfig(config, [kokoro])).toContain("no longer exists");
  });
});
