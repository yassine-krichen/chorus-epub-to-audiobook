import { useQuery } from "@tanstack/react-query";

import { fetchJobCast } from "@/lib/api/jobs";
import { shouldPollCast } from "@/lib/jobs/cast-display";
import type { CastReport, JobStatus } from "@/types/job";

const CAST_POLL_INTERVAL_MS = 5_000;

/** The job's cast report, or null while the worker has not written one (or never will).
 * A job that is still queued is not asked: it has not parsed the book yet. */
export function useJobCast(jobId: string, status: JobStatus | undefined, chaptersDone = 0) {
  return useQuery<CastReport | null>({
    queryKey: ["job-cast", jobId],
    queryFn: async () => {
      const { data } = await fetchJobCast(jobId);
      return data;
    },
    enabled: Boolean(jobId) && status !== undefined && status !== "queued",
    refetchInterval: (query) =>
      status !== undefined && shouldPollCast(status, query.state.data != null, chaptersDone)
        ? CAST_POLL_INTERVAL_MS
        : false,
  });
}
