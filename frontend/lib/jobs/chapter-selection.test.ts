import { describe, expect, it } from "vitest";

import {
  applyPick,
  listeningTime,
  selectionBounds,
  summarizeSelection,
  WHOLE_BOOK,
  type ChapterSelection,
} from "@/lib/jobs/chapter-selection";
import type { BookChapter } from "@/types/job";

function section(
  index: number,
  number: number,
  overrides: Partial<BookChapter> = {}
): BookChapter {
  return {
    index,
    number,
    title: number === 0 ? `Front ${index}` : `Chapter ${number}`,
    front_matter: false,
    word_count: 1000,
    ...overrides,
  };
}

// A synopsis (front matter), then Chapters 1 to 12, like the Shadow Slave file.
const withSynopsis: BookChapter[] = [
  section(1, 0, { title: "SYNOPSIS", front_matter: true, word_count: 120 }),
  ...Array.from({ length: 12 }, (_, i) => section(i + 2, i + 1)),
];

// A synopsis, a prologue that is part of the story, then Chapters 1 to 5.
const withPrologue: BookChapter[] = [
  section(1, 0, { title: "SYNOPSIS", front_matter: true }),
  section(2, 0, { title: "Prologue" }),
  ...Array.from({ length: 5 }, (_, i) => section(i + 3, i + 1)),
];

// Chapter 3 is split into two sections that share its number.
const withSplitChapter: BookChapter[] = [
  section(1, 1),
  section(2, 2),
  section(3, 3),
  section(4, 3),
  section(5, 4),
  section(6, 5),
];

function pickNumbers(sections: BookChapter[], selection: ChapterSelection): number[] {
  const [start, end] = selectionBounds(sections, selection);
  return sections.slice(start, end).map((s) => s.number);
}

describe("selectionBounds (copy of the server's Config.selection_bounds)", () => {
  it("starts at the first story chapter by default and skips front matter", () => {
    expect(selectionBounds(withSynopsis, WHOLE_BOOK)).toEqual([1, 13]);
  });

  it("includes front matter when the start is 0", () => {
    expect(selectionBounds(withSynopsis, { start_chapter: 0, chapter_limit: 0 })).toEqual([0, 13]);
  });

  it("counts a limit in book chapter numbers, so 10 from the start is Chapters 1 to 10", () => {
    expect(pickNumbers(withSynopsis, { start_chapter: null, chapter_limit: 10 })).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ]);
  });

  it("starts at the first section numbered at or above the start", () => {
    expect(pickNumbers(withSynopsis, { start_chapter: 5, chapter_limit: 3 })).toEqual([5, 6, 7]);
  });

  it("a kept prologue does not use up the limit", () => {
    expect(pickNumbers(withPrologue, { start_chapter: null, chapter_limit: 2 })).toEqual([0, 1, 2]);
  });

  it("returns an empty slice when no chapter is numbered that high", () => {
    expect(selectionBounds(withSynopsis, { start_chapter: 99, chapter_limit: 0 })).toEqual([13, 13]);
  });

  it("starts at the beginning for a book with nothing but front matter", () => {
    const onlyFront = [section(1, 0, { front_matter: true })];
    expect(selectionBounds(onlyFront, WHOLE_BOOK)).toEqual([0, 1]);
  });
});

describe("applyPick", () => {
  it("the first click starts at that chapter and runs to the end of the book", () => {
    const result = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 5);

    expect(result.selection).toEqual({ start_chapter: 5, chapter_limit: 0 });
    expect(result.phase).toBe("pick-end");
  });

  it("clicking the first story chapter keeps the automatic start", () => {
    const result = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 1);

    expect(result.selection.start_chapter).toBeNull();
  });

  it("clicking front matter starts at 0 so it is included", () => {
    const result = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 0);

    expect(result.selection.start_chapter).toBe(0);
  });

  it("clicking a kept prologue keeps the automatic start", () => {
    expect(applyPick(withPrologue, WHOLE_BOOK, "pick-start", 1).selection.start_chapter).toBeNull();
  });

  it("the second click ends the range at that chapter", () => {
    const started = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 3);

    const ended = applyPick(withSynopsis, started.selection, "pick-end", 7);

    expect(pickNumbers(withSynopsis, ended.selection)).toEqual([3, 4, 5, 6, 7]);
    expect(ended.phase).toBe("pick-start");
  });

  it("ending on the last chapter means to the end of the book", () => {
    const started = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 3);

    const ended = applyPick(withSynopsis, started.selection, "pick-end", 12);

    expect(ended.selection.chapter_limit).toBe(0);
    expect(pickNumbers(withSynopsis, ended.selection)).toEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  });

  it("ending on the same chapter picks just that chapter", () => {
    const started = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 4);

    const ended = applyPick(withSynopsis, started.selection, "pick-end", 4);

    expect(pickNumbers(withSynopsis, ended.selection)).toEqual([4]);
  });

  it("a second click before the start moves the start instead", () => {
    const started = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", 6);

    const moved = applyPick(withSynopsis, started.selection, "pick-end", 3);

    expect(moved.selection).toEqual({ start_chapter: 3, chapter_limit: 0 });
    expect(moved.phase).toBe("pick-end");
  });

  it("from the automatic start, Chapters 1 to 10 are a limit of 10", () => {
    const ended = applyPick(withSynopsis, WHOLE_BOOK, "pick-end", 10);

    expect(ended.selection).toEqual({ start_chapter: null, chapter_limit: 10 });
  });

  it("always shows what the server will render", () => {
    // Whatever the clicks, the shown range is selectionBounds of the resulting settings.
    for (let first = 0; first < withSynopsis.length; first += 1) {
      for (let last = first; last < withSynopsis.length; last += 1) {
        const started = applyPick(withSynopsis, WHOLE_BOOK, "pick-start", first);
        const ended = applyPick(withSynopsis, started.selection, "pick-end", last);
        const [start, end] = selectionBounds(withSynopsis, ended.selection);

        expect(start).toBe(first);
        expect(end).toBe(last + 1);
      }
    }
  });

  it("a chapter split into sections is picked whole at both ends", () => {
    const started = applyPick(withSplitChapter, WHOLE_BOOK, "pick-start", 3); // second half of 3

    const ended = applyPick(withSplitChapter, started.selection, "pick-end", 2); // first half of 3

    expect(started.selection.start_chapter).toBe(3);
    expect(pickNumbers(withSplitChapter, ended.selection)).toEqual([3, 3]);
  });

  it("ending on a prologue cannot pick it alone, so it also takes Chapter 1", () => {
    const ended = applyPick(withPrologue, WHOLE_BOOK, "pick-end", 1);

    expect(pickNumbers(withPrologue, ended.selection)).toEqual([0, 1]);
  });
});

describe("summarizeSelection", () => {
  it("counts sections and words and gives the first and last chapter numbers", () => {
    const summary = summarizeSelection(withSynopsis, { start_chapter: 2, chapter_limit: 3 });

    expect(summary).toEqual({
      chapterCount: 3,
      words: 3000,
      range: [2, 4],
      firstNumber: 2,
      lastNumber: 4,
    });
  });

  it("says nothing is selected when the start is past the end", () => {
    const summary = summarizeSelection(withSynopsis, { start_chapter: 99, chapter_limit: 0 });

    expect(summary.chapterCount).toBe(0);
    expect(summary.range).toBeNull();
  });
});

describe("listeningTime", () => {
  it("rounds to minutes and hours", () => {
    expect(listeningTime(150 * 45, 1)).toBe("45 min");
    expect(listeningTime(150 * 60 * 5, 1)).toBe("5 h");
    expect(listeningTime(150 * 90, 1)).toBe("1 h 30 min");
  });

  it("is shorter at a faster speed", () => {
    expect(listeningTime(150 * 60, 2)).toBe("30 min");
  });

  it("handles almost nothing and a bad speed", () => {
    expect(listeningTime(10, 1)).toBe("under a minute");
    expect(listeningTime(150 * 10, 0)).toBe("10 min");
  });
});
