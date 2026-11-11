import type { JobConfig } from "@/types/job";

/** Same limits the server enforces on gender overrides (book2audio.attribution.gender). */
export const MAX_CHARACTER_OVERRIDES = 100;
export const MAX_CHARACTER_NAME_LENGTH = 80;

export type OverrideGender = "m" | "f";

type OverrideMaps = Pick<JobConfig, "voice_overrides" | "gender_overrides">;

export interface CharacterOverride {
  name: string;
  gender: OverrideGender | null;
  voice: string | null;
}

export type OverrideResult =
  | { ok: true; patch: OverrideMaps }
  | { ok: false; error: string };

/** One row per character the user has said something about, by name. */
export function listCharacterOverrides(config: OverrideMaps): CharacterOverride[] {
  const names = new Set([
    ...Object.keys(config.gender_overrides),
    ...Object.keys(config.voice_overrides),
  ]);
  return [...names]
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({
      name,
      gender: config.gender_overrides[name] ?? null,
      voice: config.voice_overrides[name] ?? null,
    }));
}

/**
 * Records what the user knows about one character: their gender, a voice, or both.
 * A voice beats a gender (a chosen voice is used as it is), a gender only decides
 * which voices the character may be cast from. Saving a name again replaces its row.
 */
export function setCharacterOverride(
  config: OverrideMaps,
  rawName: string,
  choice: { gender: OverrideGender | null; voice: string | null }
): OverrideResult {
  const name = rawName.trim();
  if (!name) return { ok: false, error: "Enter the character's name." };
  if (name.length > MAX_CHARACTER_NAME_LENGTH) {
    return { ok: false, error: `Names can be at most ${MAX_CHARACTER_NAME_LENGTH} characters.` };
  }
  if (choice.gender === null && choice.voice === null) {
    return { ok: false, error: "Pick a gender, a voice, or both." };
  }

  const current = removeFrom(config, name);
  const isNewName = listCharacterOverrides(current).every((row) => row.name !== name);
  if (isNewName && listCharacterOverrides(current).length >= MAX_CHARACTER_OVERRIDES) {
    return { ok: false, error: `At most ${MAX_CHARACTER_OVERRIDES} characters can be set.` };
  }

  return {
    ok: true,
    patch: {
      gender_overrides: choice.gender
        ? { ...current.gender_overrides, [name]: choice.gender }
        : current.gender_overrides,
      voice_overrides: choice.voice
        ? { ...current.voice_overrides, [name]: choice.voice }
        : current.voice_overrides,
    },
  };
}

export function removeCharacterOverride(config: OverrideMaps, name: string): OverrideMaps {
  return removeFrom(config, name);
}

function removeFrom(config: OverrideMaps, name: string): OverrideMaps {
  const genders = { ...config.gender_overrides };
  const voices = { ...config.voice_overrides };
  delete genders[name];
  delete voices[name];
  return { gender_overrides: genders, voice_overrides: voices };
}
