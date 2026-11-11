import { describe, expect, it } from "vitest";

import { partChapterRange } from "@/lib/jobs/part-chapter-range";
import type { PartProgress } from "@/types/job";

function part(overrides: Partial<PartProgress>): PartProgress {
  return {
    part_number: 1,
    first_index: 2,
    last_index: 11,
    r2_key: "jobs/1/part-01.m4b",
    status: "done",
    download_url: null,
    ...overrides,
  };
}

describe("partChapterRange", () => {
  it("shows the book chapter numbers when the part has them, with no offset", () => {
    expect(partChapterRange(part({ first_number: 1, last_number: 10 }))).toBe("Chapters 1–10");
  });

  it("falls back to the position index for parts stored before numbering existed", () => {
    expect(partChapterRange(part({}))).toBe("Chapters 2–11");
  });

  it("keeps a first number of 0 instead of falling back", () => {
    expect(partChapterRange(part({ first_number: 0, last_number: 9 }))).toBe("Chapters 0–9");
  });
});
