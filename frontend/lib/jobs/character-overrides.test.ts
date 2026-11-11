import { describe, expect, it } from "vitest";

import {
  listCharacterOverrides,
  MAX_CHARACTER_NAME_LENGTH,
  MAX_CHARACTER_OVERRIDES,
  removeCharacterOverride,
  setCharacterOverride,
} from "@/lib/jobs/character-overrides";

const empty = { voice_overrides: {}, gender_overrides: {} };

describe("setCharacterOverride", () => {
  it("records a gender on its own", () => {
    const result = setCharacterOverride(empty, "Cassie", { gender: "f", voice: null });

    expect(result).toEqual({
      ok: true,
      patch: { gender_overrides: { Cassie: "f" }, voice_overrides: {} },
    });
  });

  it("records a voice on its own", () => {
    const result = setCharacterOverride(empty, "Cassie", { gender: null, voice: "af_sky" });

    expect(result).toEqual({
      ok: true,
      patch: { gender_overrides: {}, voice_overrides: { Cassie: "af_sky" } },
    });
  });

  it("records both and trims the name", () => {
    const result = setCharacterOverride(empty, "  Cassie ", { gender: "f", voice: "af_sky" });

    expect(result).toEqual({
      ok: true,
      patch: { gender_overrides: { Cassie: "f" }, voice_overrides: { Cassie: "af_sky" } },
    });
  });

  it("replaces an earlier choice for the same name instead of keeping both", () => {
    const before = { gender_overrides: { Cassie: "f" as const }, voice_overrides: { Cassie: "af_sky" } };

    const result = setCharacterOverride(before, "Cassie", { gender: "m", voice: null });

    expect(result).toEqual({
      ok: true,
      patch: { gender_overrides: { Cassie: "m" }, voice_overrides: {} },
    });
  });

  it("leaves the other characters alone", () => {
    const before = { gender_overrides: { Kai: "m" as const }, voice_overrides: {} };

    const result = setCharacterOverride(before, "Cassie", { gender: "f", voice: null });

    expect(result).toMatchObject({
      ok: true,
      patch: { gender_overrides: { Kai: "m", Cassie: "f" } },
    });
  });

  it("refuses an empty name", () => {
    expect(setCharacterOverride(empty, "   ", { gender: "f", voice: null })).toEqual({
      ok: false,
      error: "Enter the character's name.",
    });
  });

  it("refuses a name longer than the server accepts", () => {
    const result = setCharacterOverride(empty, "x".repeat(MAX_CHARACTER_NAME_LENGTH + 1), {
      gender: "f",
      voice: null,
    });

    expect(result.ok).toBe(false);
  });

  it("refuses a row with neither a gender nor a voice", () => {
    expect(setCharacterOverride(empty, "Cassie", { gender: null, voice: null }).ok).toBe(false);
  });

  it("refuses a new character once the server's limit is reached", () => {
    const full = {
      voice_overrides: {},
      gender_overrides: Object.fromEntries(
        Array.from({ length: MAX_CHARACTER_OVERRIDES }, (_, i) => [`Name ${i}`, "m" as const])
      ),
    };

    expect(setCharacterOverride(full, "One more", { gender: "f", voice: null }).ok).toBe(false);
    expect(setCharacterOverride(full, "Name 3", { gender: "f", voice: null }).ok).toBe(true);
  });
});

describe("listCharacterOverrides", () => {
  it("merges gender and voice rows by name, sorted", () => {
    const rows = listCharacterOverrides({
      gender_overrides: { Kai: "m", Cassie: "f" },
      voice_overrides: { Cassie: "af_sky", Zed: "am_adam" },
    });

    expect(rows).toEqual([
      { name: "Cassie", gender: "f", voice: "af_sky" },
      { name: "Kai", gender: "m", voice: null },
      { name: "Zed", gender: null, voice: "am_adam" },
    ]);
  });
});

describe("removeCharacterOverride", () => {
  it("drops both the gender and the voice for a name", () => {
    const result = removeCharacterOverride(
      { gender_overrides: { Cassie: "f", Kai: "m" }, voice_overrides: { Cassie: "af_sky" } },
      "Cassie"
    );

    expect(result).toEqual({ gender_overrides: { Kai: "m" }, voice_overrides: {} });
  });
});
