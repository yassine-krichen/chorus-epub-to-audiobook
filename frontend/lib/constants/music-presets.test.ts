import { describe, expect, it } from "vitest";

import { MUSIC_PRESETS } from "@/lib/constants/music-presets";

describe("MUSIC_PRESETS", () => {
  // This constant is manually synced with the Python MUSIC_PRESETS dict in
  // src/book2audio/assemble/music.py. Pinning the exact values here makes
  // any future drift between the two fail loudly instead of silently.
  it("matches the backend's MUSIC_PRESETS keys exactly", () => {
    expect(MUSIC_PRESETS).toEqual(["ambient", "drone", "rain"]);
  });
});
