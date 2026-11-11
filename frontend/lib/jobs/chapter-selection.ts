import type { BookChapter } from "@/types/job";

/** The two settings the server uses to pick chapters (see book2audio Config.selection_bounds). */
export interface ChapterSelection {
  /** null = automatic (skip front matter), 0 = include front matter, N = book chapter N. */
  start_chapter: number | null;
  /** 0 = through the end of the book, otherwise that many book numbers from the start. */
  chapter_limit: number;
}

/** Where the picker is in its two-click flow: waiting for the first chapter or the last one. */
export type PickPhase = "pick-start" | "pick-end";

type Section = Pick<BookChapter, "number" | "front_matter">;

// A calm narration pace, used only for the "about N hours" hint.
const WORDS_PER_MINUTE = 150;

/**
 * The 0-based ``[start, end)`` slice of ``sections`` the server will render for these
 * settings. A line-by-line copy of ``Config.selection_bounds`` in the backend: the picker
 * shows what this returns, so the two have to agree.
 */
export function selectionBounds(
  sections: readonly Section[],
  selection: ChapterSelection
): [number, number] {
  const total = sections.length;
  let startPos: number;
  if (selection.start_chapter === null) {
    const firstStory = sections.findIndex((section) => !section.front_matter);
    startPos = firstStory === -1 ? 0 : firstStory;
  } else if (selection.start_chapter === 0) {
    startPos = 0;
  } else {
    const wanted = selection.start_chapter;
    const found = sections.findIndex((section) => section.number >= wanted);
    startPos = found === -1 ? total : found;
  }
  if (startPos >= total || selection.chapter_limit === 0) return [startPos, total];

  // A kept prologue (number 0, not front matter) does not use up the limit.
  const first = sections[startPos];
  const countedFrom = first.front_matter ? first.number : Math.max(first.number, 1);
  const stopNumber = countedFrom + selection.chapter_limit;
  let endPos = total;
  for (let i = startPos; i < total; i += 1) {
    if (sections[i].number >= stopNumber) {
      endPos = i;
      break;
    }
  }
  return [startPos, endPos];
}

/** The selection that means "everything from the story's first chapter". */
export const WHOLE_BOOK: ChapterSelection = { start_chapter: null, chapter_limit: 0 };

/** Whether two sections are parts of the same chapter: same book number, and both story or
 * both front matter (a synopsis and a prologue are both numbered 0 but are not one chapter). */
function sameChapter(a: Section, b: Section): boolean {
  return a.number === b.number && a.front_matter === b.front_matter;
}

/** Position of the first section of the chapter at ``pos``, so a chapter split into
 * several sections is always picked whole. */
function groupStart(sections: readonly Section[], pos: number): number {
  let start = pos;
  while (start > 0 && sameChapter(sections[start - 1], sections[pos])) start -= 1;
  return start;
}

function groupEnd(sections: readonly Section[], pos: number): number {
  let end = pos;
  while (end < sections.length - 1 && sameChapter(sections[end + 1], sections[pos])) end += 1;
  return end;
}

function startSettingFor(sections: readonly Section[], pos: number): number | null {
  if (sections[pos].front_matter) return 0; // front matter is always at the very beginning
  const firstStory = sections.findIndex((section) => !section.front_matter);
  return pos === firstStory ? null : sections[pos].number;
}

/**
 * What a click on the section at ``pos`` does.
 *
 * First click: start there and go to the end of the book. Second click on a later (or the
 * same) chapter: end there. A second click before the start just moves the start.
 */
export function applyPick(
  sections: readonly Section[],
  current: ChapterSelection,
  phase: PickPhase,
  pos: number
): { selection: ChapterSelection; phase: PickPhase } {
  const [startPos] = selectionBounds(sections, current);
  const canEnd = phase === "pick-end" && pos >= startPos;

  if (!canEnd) {
    const start = groupStart(sections, pos);
    return {
      selection: { start_chapter: startSettingFor(sections, start), chapter_limit: 0 },
      phase: "pick-end",
    };
  }

  const last = groupEnd(sections, pos);
  if (last >= sections.length - 1) {
    return { selection: { ...current, chapter_limit: 0 }, phase: "pick-start" };
  }
  const first = sections[startPos];
  const countedFrom = first.front_matter ? first.number : Math.max(first.number, 1);
  // At least 1: a limit of 0 would mean "to the end of the book".
  const limit = Math.max(sections[last + 1].number - countedFrom, 1);
  return { selection: { ...current, chapter_limit: limit }, phase: "pick-start" };
}

export interface SelectionSummary {
  chapterCount: number;
  words: number;
  /** Position of the first and last selected section, or null when nothing is selected. */
  range: [number, number] | null;
  firstNumber: number | null;
  lastNumber: number | null;
}

export function summarizeSelection(
  sections: readonly BookChapter[],
  selection: ChapterSelection
): SelectionSummary {
  const [start, end] = selectionBounds(sections, selection);
  const picked = sections.slice(start, end);
  if (picked.length === 0) {
    return { chapterCount: 0, words: 0, range: null, firstNumber: null, lastNumber: null };
  }
  return {
    chapterCount: picked.length,
    words: picked.reduce((sum, section) => sum + section.word_count, 0),
    range: [start, end - 1],
    firstNumber: picked[0].number,
    lastNumber: picked[picked.length - 1].number,
  };
}

/** Rough listening time, so a user choosing chapters can see what they are asking for. */
export function listeningTime(words: number, speed: number): string {
  const minutes = Math.round(words / WORDS_PER_MINUTE / (speed > 0 ? speed : 1));
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}
