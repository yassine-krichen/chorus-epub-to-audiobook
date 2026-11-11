import type { JobDetail } from "@/types/job";

/** Human-meaningful phase of a render, derived from job/chapter/part state.
 * Pure, side-effect-free so it's trivially unit-testable. */
export type RenderStage =
  | { kind: "succeeded" }
  | { kind: "failed" }
  | { kind: "canceled" }
  | { kind: "preparing-input" }
  | { kind: "casting-voices" }
  | { kind: "assembling-part"; partNumber: number; totalParts: number }
  | { kind: "synthesizing-chapter"; chapterNumber: number; totalChapters: number };

interface Batch {
  partNumber: number;
  firstIndex: number;
  lastIndex: number;
}

/** Mirrors ``pipeline.py::_plan_batches``: the ``totalChapters`` selected
 * chapters grouped into batches of ``batchSize``, expressed as 1-based
 * positions within the selection (not book chapter numbers, which depend on
 * where the book starts). ``batchSize <= 0`` (or a size covering everything)
 * yields a single batch. */
function planBatches(totalChapters: number, batchSize: number): Batch[] {
  if (batchSize <= 0 || batchSize >= totalChapters) {
    return [{ partNumber: 1, firstIndex: 1, lastIndex: totalChapters }];
  }
  const batches: Batch[] = [];
  for (let start = 0; start < totalChapters; start += batchSize) {
    const lastIndex = Math.min(start + batchSize, totalChapters);
    batches.push({ partNumber: batches.length + 1, firstIndex: start + 1, lastIndex });
  }
  return batches;
}

export function deriveRenderStage(job: JobDetail): RenderStage {
  if (job.status === "succeeded" || job.status === "failed" || job.status === "canceled") {
    return { kind: job.status };
  }

  if (job.total_chapters == null) {
    return { kind: "preparing-input" };
  }

  if (job.chapters.length === 0) {
    return { kind: "casting-voices" };
  }

  const totalChapters = job.total_chapters;
  // Position within the selection, so "chapter 3 of 10" holds whatever the
  // start_chapter setting is.
  const nextChapterNumber = job.chapters.length + 1;
  const batches = planBatches(totalChapters, job.config.batch_size);
  const currentBatch =
    batches.find((batch) => nextChapterNumber <= batch.lastIndex) ?? batches[batches.length - 1];

  const batchChaptersDone = nextChapterNumber > currentBatch.lastIndex;
  if (batchChaptersDone) {
    const partReady = job.parts.some((part) => part.part_number === currentBatch.partNumber);
    if (!partReady) {
      return {
        kind: "assembling-part",
        partNumber: currentBatch.partNumber,
        totalParts: batches.length,
      };
    }
  }

  return { kind: "synthesizing-chapter", chapterNumber: nextChapterNumber, totalChapters };
}
