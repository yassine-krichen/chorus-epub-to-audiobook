import { useQuery } from "@tanstack/react-query";

import { inspectEpub } from "@/lib/api/jobs";
import type { ApiResult } from "@/lib/api/client";
import type { BookInspection } from "@/types/job";

/** The chapters of an uploaded book. Reads the book once per upload (it never changes),
 * and keeps the API's message when it cannot be read so the wizard can say why. */
export function useBookInspection(objectKey: string | null) {
  return useQuery<ApiResult<BookInspection>>({
    queryKey: ["book-inspection", objectKey],
    queryFn: () => inspectEpub(objectKey as string),
    enabled: objectKey !== null,
    staleTime: Infinity,
    retry: false,
  });
}
