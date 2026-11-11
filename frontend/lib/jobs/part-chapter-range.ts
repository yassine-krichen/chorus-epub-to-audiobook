import type { PartProgress } from "@/types/job";

/** The chapter range a part covers, in the book's own chapter numbers.
 * Parts stored before numbering existed only carry the position index. */
export function partChapterRange(part: PartProgress): string {
  const first = part.first_number ?? part.first_index;
  const last = part.last_number ?? part.last_index;
  return `Chapters ${first}–${last}`;
}
