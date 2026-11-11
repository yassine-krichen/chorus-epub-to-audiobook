import type { CastCharacter, CastGender, CastReport } from "@/types/job";

const GENDER_LABELS: Record<"m" | "f", string> = { m: "Male", f: "Female" };

// What each kind of evidence means to a reader, singular and plural.
const EVIDENCE_LABELS: Record<string, [string, string]> = {
  pronoun: ["pronoun", "pronouns"],
  title: ["title", "titles"],
  kinship: ["family tie", "family ties"],
  appositive: ["description", "descriptions"],
  override: ["your choice", "your choice"],
};

export function genderLabel(gender: CastGender): string {
  return gender === null ? "Not sure" : GENDER_LABELS[gender];
}

/** True when the narrator's voice reads this character (not sure of gender, or sent there). */
export function isReadByNarrator(character: CastCharacter, narratorVoice: string): boolean {
  return character.voice_source === "narrator" || character.voice === narratorVoice;
}

/** "3 pronouns, 1 title" from the evidence counts; says so when the text gave no clue. */
export function evidenceSummary(layerCounts: Record<string, number>): string {
  const parts = Object.entries(layerCounts)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([layer, count]) => {
      const [singular, plural] = EVIDENCE_LABELS[layer] ?? [layer, layer];
      return layer === "override" ? singular : `${count} ${count === 1 ? singular : plural}`;
    });
  return parts.length > 0 ? parts.join(", ") : "No clues in the text";
}

export interface CastSummary {
  speakers: number;
  /** Characters whose gender is unknown, so the narrator reads them. */
  notSure: number;
  readByNarrator: number;
}

export function summarizeCast(report: CastReport): CastSummary {
  const characters = report.characters;
  return {
    speakers: characters.length,
    notSure: characters.filter((c) => c.gender === null).length,
    readByNarrator: characters.filter((c) => isReadByNarrator(c, report.narrator_voice)).length,
  };
}

/** The cast stays hidden until the worker has written it; it never changes mid-render. */
export function shouldPollCast(status: string, hasReport: boolean): boolean {
  return !hasReport && (status === "running" || status === "awaiting_cast_review");
}
