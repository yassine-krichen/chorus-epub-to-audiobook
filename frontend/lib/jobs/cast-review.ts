import type { CastReport, CastReviewEdit, CastReviewRequest } from "@/types/job";

/** What the user decided to do with one detected character. */
export type TreatAs =
  | { kind: "own" }
  | { kind: "narrator" }
  | { kind: "merge"; into: string };

export interface CharacterDraft {
  /** A gender picked in the review; null keeps what was detected. */
  gender: "m" | "f" | null;
  /** A voice picked in the review; null keeps the automatic one. */
  voice: string | null;
  treatAs: TreatAs;
}

/** Drafts by character name. A character with no entry is left as detected. */
export type DraftMap = Record<string, CharacterDraft>;

const UNCHANGED: CharacterDraft = { gender: null, voice: null, treatAs: { kind: "own" } };

export function draftFor(drafts: DraftMap, name: string): CharacterDraft {
  return drafts[name] ?? UNCHANGED;
}

function isUnchanged(draft: CharacterDraft): boolean {
  return draft.gender === null && draft.voice === null && draft.treatAs.kind === "own";
}

/** Returns the drafts with ``patch`` applied to one character; a draft that is back to
 * "as detected" is dropped, so the map only holds real decisions. */
export function updateDraft(
  drafts: DraftMap,
  name: string,
  patch: Partial<CharacterDraft>
): DraftMap {
  const next = { ...drafts };
  const draft = { ...draftFor(drafts, name), ...patch };
  if (isUnchanged(draft)) {
    delete next[name];
  } else {
    next[name] = draft;
  }
  return next;
}

/** True when another character has been merged into ``name``. */
export function hasMergedInto(drafts: DraftMap, name: string): boolean {
  return Object.values(drafts).some((d) => d.treatAs.kind === "merge" && d.treatAs.into === name);
}

/**
 * Whether ``name`` may be merged away or sent to the narrator. A character that others
 * were merged into has to stay a character of its own: otherwise their lines would
 * follow it into nothing. The server refuses such chains too.
 */
export function canBeReplaced(drafts: DraftMap, name: string): boolean {
  return !hasMergedInto(drafts, name);
}

/** The characters ``name`` may be merged into: the others that are not merged away themselves. */
export function mergeTargets(report: CastReport, drafts: DraftMap, name: string): string[] {
  if (!canBeReplaced(drafts, name)) return [];
  return report.characters
    .map((character) => character.name)
    .filter((other) => other !== name && draftFor(drafts, other).treatAs.kind === "own");
}

/** The edits to send: one per character the user changed, nothing for the rest. */
export function buildReviewRequest(report: CastReport, drafts: DraftMap): CastReviewRequest {
  const characters: CastReviewEdit[] = [];
  for (const character of report.characters) {
    const draft = draftFor(drafts, character.name);
    const { treatAs } = draft;
    if (treatAs.kind === "merge") {
      // A merge into a character that is itself replaced would be a chain: skip it.
      if (draftFor(drafts, treatAs.into).treatAs.kind === "own") {
        characters.push({ name: character.name, merge_into: treatAs.into });
      }
    } else if (treatAs.kind === "narrator") {
      characters.push({ name: character.name, read_by_narrator: true });
    } else {
      const edit: CastReviewEdit = { name: character.name };
      // Picking the gender that was already detected changes nothing.
      if (draft.gender !== null && draft.gender !== character.gender) edit.gender = draft.gender;
      if (draft.voice !== null) edit.voice = draft.voice;
      if (edit.gender !== undefined || edit.voice !== undefined) characters.push(edit);
    }
  }
  return { characters };
}

export function changeCount(report: CastReport, drafts: DraftMap): number {
  return buildReviewRequest(report, drafts).characters.length;
}

/** The <Select> value for a "reads as" choice. Names cannot clash with the fixed values
 * because merge values carry a prefix. */
export function treatAsToValue(treatAs: TreatAs): string {
  if (treatAs.kind === "merge") return `merge:${treatAs.into}`;
  return treatAs.kind;
}

export function valueToTreatAs(value: string): TreatAs {
  if (value === "narrator") return { kind: "narrator" };
  if (value.startsWith("merge:")) return { kind: "merge", into: value.slice("merge:".length) };
  return { kind: "own" };
}

/** "Mon, 3 Nov, 14:05" style text for when the review lapses; empty for a bad date. */
export function formatDeadline(iso: string | null | undefined, locale?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
