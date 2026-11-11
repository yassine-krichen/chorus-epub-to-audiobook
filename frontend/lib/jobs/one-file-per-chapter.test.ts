import { describe, expect, it } from "vitest";

import { oneFilePerChapterPatch } from "@/lib/jobs/one-file-per-chapter";
import { PER_CHAPTER_FILENAME_TEMPLATE } from "@/lib/validation/filename-template";
import { DEFAULT_JOB_CONFIG } from "@/types/job";

describe("oneFilePerChapterPatch", () => {
  it("turning it on sets batch size 1 and swaps the untouched default template", () => {
    expect(
      oneFilePerChapterPatch(true, { filename_template: DEFAULT_JOB_CONFIG.filename_template })
    ).toEqual({ batch_size: 1, filename_template: PER_CHAPTER_FILENAME_TEMPLATE });
  });

  it("turning it on keeps a template the user wrote themselves", () => {
    expect(oneFilePerChapterPatch(true, { filename_template: "{title} ch{chapter}" })).toEqual({
      batch_size: 1,
      filename_template: "{title} ch{chapter}",
    });
  });

  it("turning it on fills in the per-chapter template when none is set", () => {
    expect(oneFilePerChapterPatch(true, { filename_template: null })).toEqual({
      batch_size: 1,
      filename_template: PER_CHAPTER_FILENAME_TEMPLATE,
    });
  });

  it("turning it off restores the default batch size and part template", () => {
    expect(
      oneFilePerChapterPatch(false, { filename_template: PER_CHAPTER_FILENAME_TEMPLATE })
    ).toEqual({
      batch_size: DEFAULT_JOB_CONFIG.batch_size,
      filename_template: DEFAULT_JOB_CONFIG.filename_template,
    });
  });

  it("turning it off replaces a custom template that needs {chapter_title}", () => {
    expect(oneFilePerChapterPatch(false, { filename_template: "{chapter_title} only" })).toEqual({
      batch_size: DEFAULT_JOB_CONFIG.batch_size,
      filename_template: DEFAULT_JOB_CONFIG.filename_template,
    });
  });

  it("turning it off keeps a custom template that works for larger parts", () => {
    expect(oneFilePerChapterPatch(false, { filename_template: "{title} ch{chapter}" })).toEqual({
      batch_size: DEFAULT_JOB_CONFIG.batch_size,
      filename_template: "{title} ch{chapter}",
    });
  });
});
