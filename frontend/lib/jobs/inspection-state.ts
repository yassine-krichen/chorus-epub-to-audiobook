import type { ApiResult } from "@/lib/api/client";
import type { BookInspection } from "@/types/job";

/** What the chapter picker has to show about the uploaded book. */
export type InspectionState =
  | { status: "no-upload" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; book: BookInspection };

function withFullStop(message: string): string {
  return /[.!?]$/.test(message) ? message : `${message}.`;
}

/** Folds the upload state and the book-reading request into one thing for the picker to render. */
export function inspectionStateOf(
  uploadDone: boolean,
  query: { data: ApiResult<BookInspection> | undefined }
): InspectionState {
  if (!uploadDone) return { status: "no-upload" };
  if (query.data === undefined) return { status: "loading" };
  const { data, error } = query.data;
  if (error !== null || data === null) {
    return { status: "error", message: withFullStop(error ?? "The book could not be read") };
  }
  if (data.chapters.length === 0) {
    return { status: "error", message: "The book has no chapters we can read." };
  }
  return { status: "ready", book: data };
}
