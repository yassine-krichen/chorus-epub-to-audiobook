import { describe, expect, it } from "vitest";

import { inspectionStateOf } from "@/lib/jobs/inspection-state";
import type { BookInspection } from "@/types/job";

const book: BookInspection = {
  title: "Test Book",
  author: "Jane",
  chapters: [{ index: 1, number: 1, title: "Chapter 1", front_matter: false, word_count: 100 }],
};

describe("inspectionStateOf", () => {
  it("has nothing to show before the upload has finished", () => {
    expect(inspectionStateOf(false, { data: undefined })).toEqual({ status: "no-upload" });
  });

  it("ignores a stale result while a new file is still uploading", () => {
    expect(inspectionStateOf(false, { data: { data: book, error: null } })).toEqual({
      status: "no-upload",
    });
  });

  it("is loading while the book is being read", () => {
    expect(inspectionStateOf(true, { data: undefined })).toEqual({ status: "loading" });
  });

  it("is ready with the book once it was read", () => {
    expect(inspectionStateOf(true, { data: { data: book, error: null } })).toEqual({
      status: "ready",
      book,
    });
  });

  it("keeps the server's message when the book could not be read, ending it with a full stop", () => {
    const state = inspectionStateOf(true, {
      data: { data: null, error: "This file could not be read as an EPUB" },
    });

    expect(state).toEqual({
      status: "error",
      message: "This file could not be read as an EPUB.",
    });
  });

  it("does not add a second full stop", () => {
    const state = inspectionStateOf(true, { data: { data: null, error: "Too many books." } });

    expect(state).toEqual({ status: "error", message: "Too many books." });
  });

  it("treats a book without chapters as an error", () => {
    const state = inspectionStateOf(true, { data: { data: { ...book, chapters: [] }, error: null } });

    expect(state.status).toBe("error");
  });
});
