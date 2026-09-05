import { describe, expect, it } from "vitest";

import { deriveRenderStage } from "@/lib/jobs/render-stage";
import {
  DEFAULT_JOB_CONFIG,
  type ChapterProgress,
  type JobConfig,
  type JobDetail,
  type PartProgress,
} from "@/types/job";

function baseJob(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: "job-1",
    title: "Test Book",
    status: "running",
    total_chapters: null,
    progress_percent: 0,
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
    epub_object_key: "uploads/job-1.epub",
    config: DEFAULT_JOB_CONFIG,
    chapters: [],
    parts: [],
    error_message: null,
    ...overrides,
  };
}

function withBatchSize(batchSize: number): JobConfig {
  return { ...DEFAULT_JOB_CONFIG, batch_size: batchSize };
}

function withBatchSizeAndStartChapter(batchSize: number, startChapter: number): JobConfig {
  return { ...DEFAULT_JOB_CONFIG, batch_size: batchSize, start_chapter: startChapter };
}

function makeChapters(count: number): ChapterProgress[] {
  return Array.from({ length: count }, (_, i) => ({
    index: i + 1,
    title: `Chapter ${i + 1}`,
    status: "done",
    thumbnail_status: null,
    thumbnail_url: null,
  }));
}

function makePart(
  partNumber: number,
  firstIndex: number,
  lastIndex: number
): PartProgress {
  return {
    part_number: partNumber,
    first_index: firstIndex,
    last_index: lastIndex,
    r2_key: `parts/job-1/part-${partNumber}.m4b`,
    status: "ready",
    download_url: null,
  };
}

describe("deriveRenderStage", () => {
  describe("terminal statuses", () => {
    it("returns succeeded for a job with status succeeded", () => {
      const job = baseJob({ status: "succeeded", total_chapters: 5 });
      expect(deriveRenderStage(job)).toEqual({ kind: "succeeded" });
    });

    it("returns failed for a job with status failed", () => {
      const job = baseJob({ status: "failed", total_chapters: 5 });
      expect(deriveRenderStage(job)).toEqual({ kind: "failed" });
    });

    it("returns canceled for a job with status canceled", () => {
      const job = baseJob({ status: "canceled", total_chapters: 5 });
      expect(deriveRenderStage(job)).toEqual({ kind: "canceled" });
    });

    it("short-circuits on terminal status even when total_chapters is still null", () => {
      const job = baseJob({ status: "succeeded", total_chapters: null });
      expect(deriveRenderStage(job)).toEqual({ kind: "succeeded" });
    });

    it("short-circuits on terminal status even when chapters and parts look mid-render", () => {
      const job = baseJob({
        status: "failed",
        total_chapters: 6,
        config: withBatchSize(3),
        chapters: makeChapters(3),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({ kind: "failed" });
    });
  });

  describe("preparing-input", () => {
    it("returns preparing-input when total_chapters is null for a running job", () => {
      const job = baseJob({ status: "running", total_chapters: null });
      expect(deriveRenderStage(job)).toEqual({ kind: "preparing-input" });
    });

    it("returns preparing-input when total_chapters is null for a queued job", () => {
      const job = baseJob({ status: "queued", total_chapters: null });
      expect(deriveRenderStage(job)).toEqual({ kind: "preparing-input" });
    });
  });

  describe("casting-voices", () => {
    it("returns casting-voices when total_chapters is known but no chapters have started yet", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 10,
        chapters: [],
      });
      expect(deriveRenderStage(job)).toEqual({ kind: "casting-voices" });
    });
  });

  describe("synthesizing-chapter", () => {
    it("reports chapter 2 of 5 after the first chapter completes", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSize(10),
        chapters: makeChapters(1),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 2,
        totalChapters: 5,
      });
    });

    it("reports chapter 4 of 10 mid-render in single-batch mode", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 10,
        config: withBatchSize(10),
        chapters: makeChapters(3),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 4,
        totalChapters: 10,
      });
    });

    it("reports chapter 3 of 5 when batch_size is 0 (single-file mode)", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSize(0),
        chapters: makeChapters(2),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 3,
        totalChapters: 5,
      });
    });

    it("moves straight into the next batch's first chapter once a non-final batch completes, without an intermediate assembling stage", () => {
      // total=7, batch_size=3 => batches [1-3], [4-6], [7-7]. Batch 1 is
      // fully done (3 chapters), but there's still a next batch to
      // synthesize, so this should read as chapter 4, not "assembling part 1".
      const job = baseJob({
        status: "running",
        total_chapters: 7,
        config: withBatchSize(3),
        chapters: makeChapters(3),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 4,
        totalChapters: 7,
      });
    });

    it("reports chapter 7 of 6 when the final part is already ready but job status hasn't flipped to succeeded yet", () => {
      // Degenerate but valid input for this pure function: all chapters and
      // the final part are done, yet status is still "running". nextChapterNumber
      // (7) exceeds totalChapters (6) in this case.
      const job = baseJob({
        status: "running",
        total_chapters: 6,
        config: withBatchSize(3),
        chapters: makeChapters(6),
        parts: [makePart(1, 1, 3), makePart(2, 4, 6)],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 7,
        totalChapters: 6,
      });
    });
  });

  describe("assembling-part", () => {
    it("reports assembling part 2 of 2 once the final batch's chapters are all done but its part hasn't appeared yet", () => {
      // total=6, batch_size=3 => batches [1-3] part 1, [4-6] part 2.
      const job = baseJob({
        status: "running",
        total_chapters: 6,
        config: withBatchSize(3),
        chapters: makeChapters(6),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "assembling-part",
        partNumber: 2,
        totalParts: 2,
      });
    });

    it("falls back to synthesizing-chapter once the final batch's part has appeared in job.parts", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 6,
        config: withBatchSize(3),
        chapters: makeChapters(6),
        parts: [makePart(1, 1, 3), makePart(2, 4, 6)],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 7,
        totalChapters: 6,
      });
    });

    it("reports assembling part 1 of 1 for a single-batch job (batch_size 0) once all chapters are done and no part exists yet", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSize(0),
        chapters: makeChapters(5),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "assembling-part",
        partNumber: 1,
        totalParts: 1,
      });
    });

    it("does not report assembling-part for an intermediate batch even when that batch's own chapters are all complete", () => {
      // Same total/batch_size as above, but only the first batch (chapters
      // 1-3) is done; the last batch (chapters 7-7) has zero chapters done,
      // so batchChaptersDone is false and this must not read as assembling.
      const job = baseJob({
        status: "running",
        total_chapters: 7,
        config: withBatchSize(3),
        chapters: makeChapters(3),
        parts: [makePart(1, 1, 3)],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 4,
        totalChapters: 7,
      });
    });
  });

  describe("start_chapter offset (start_chapter > 1)", () => {
    it("returns casting-voices when total_chapters is known but no chapters have started yet, offset from start_chapter", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSizeAndStartChapter(10, 10),
        chapters: [],
      });
      expect(deriveRenderStage(job)).toEqual({ kind: "casting-voices" });
    });

    it("reports absolute chapter 11 of 5 (selection starting at chapter 10) after the first selected chapter completes", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSizeAndStartChapter(10, 10),
        chapters: makeChapters(1),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 11,
        totalChapters: 5,
      });
    });

    it("reports absolute chapter 13 of 10 mid-render in single-batch mode starting at chapter 10", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 10,
        config: withBatchSizeAndStartChapter(10, 10),
        chapters: makeChapters(3),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 13,
        totalChapters: 10,
      });
    });

    it("reports absolute chapter 12 of 5 when batch_size is 0 (single-file mode) starting at chapter 10", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSizeAndStartChapter(0, 10),
        chapters: makeChapters(2),
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 12,
        totalChapters: 5,
      });
    });

    it("moves straight into the next batch's first absolute chapter once a non-final batch completes, offset from start_chapter", () => {
      // selection = absolute chapters 10-16 (total=7), batch_size=3 =>
      // batches [10-12], [13-15], [16-16]. First batch is fully done (3
      // chapters), so this should read as absolute chapter 13, not an
      // intermediate assembling stage.
      const job = baseJob({
        status: "running",
        total_chapters: 7,
        config: withBatchSizeAndStartChapter(3, 10),
        chapters: makeChapters(3),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 13,
        totalChapters: 7,
      });
    });

    it("does not report assembling-part for an intermediate batch even when that batch's own chapters are all complete, offset from start_chapter", () => {
      // Same total/batch_size as above, but only the first batch (absolute
      // chapters 10-12) is done; the last batch (16-16) has zero chapters
      // done, so batchChaptersDone is false and this must not read as
      // assembling.
      const job = baseJob({
        status: "running",
        total_chapters: 7,
        config: withBatchSizeAndStartChapter(3, 10),
        chapters: makeChapters(3),
        parts: [makePart(1, 10, 12)],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 13,
        totalChapters: 7,
      });
    });

    it("reports assembling part 2 of 2 once the final batch's chapters are all done but its part hasn't appeared yet, offset from start_chapter", () => {
      // selection = absolute chapters 10-15 (total=6), batch_size=3 =>
      // batches [10-12] part 1, [13-15] part 2.
      const job = baseJob({
        status: "running",
        total_chapters: 6,
        config: withBatchSizeAndStartChapter(3, 10),
        chapters: makeChapters(6),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "assembling-part",
        partNumber: 2,
        totalParts: 2,
      });
    });

    it("falls back to synthesizing-chapter once the final batch's part has appeared in job.parts, offset from start_chapter", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 6,
        config: withBatchSizeAndStartChapter(3, 10),
        chapters: makeChapters(6),
        parts: [makePart(1, 10, 12), makePart(2, 13, 15)],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "synthesizing-chapter",
        chapterNumber: 16,
        totalChapters: 6,
      });
    });

    it("reports assembling part 1 of 1 for a single-batch job (batch_size 0) offset from start_chapter once all chapters are done and no part exists yet", () => {
      const job = baseJob({
        status: "running",
        total_chapters: 5,
        config: withBatchSizeAndStartChapter(0, 10),
        chapters: makeChapters(5),
        parts: [],
      });
      expect(deriveRenderStage(job)).toEqual({
        kind: "assembling-part",
        partNumber: 1,
        totalParts: 1,
      });
    });
  });
});
