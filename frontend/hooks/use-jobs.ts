import { useQuery } from "@tanstack/react-query";

import { fetchJob, fetchJobs } from "@/lib/api/jobs";
import { ACTIVE_JOB_STATUSES, type JobDetail, type JobListResponse } from "@/types/job";

const LIST_POLL_INTERVAL_MS = 5_000;
const DETAIL_POLL_INTERVAL_MS = 4_000;

export function useJobs(page: number, pageSize = 20) {
  return useQuery<JobListResponse | null>({
    queryKey: ["jobs", page, pageSize],
    queryFn: async () => {
      const { data } = await fetchJobs(page, pageSize);
      return data;
    },
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      const hasActiveJob = jobs.some((job) => ACTIVE_JOB_STATUSES.includes(job.status));
      return hasActiveJob ? LIST_POLL_INTERVAL_MS : false;
    },
  });
}

export function useJob(jobId: string) {
  return useQuery<JobDetail | null>({
    queryKey: ["job", jobId],
    queryFn: async () => {
      const { data } = await fetchJob(jobId);
      return data;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status) return false;
      return ACTIVE_JOB_STATUSES.includes(status) ? DETAIL_POLL_INTERVAL_MS : false;
    },
    enabled: Boolean(jobId),
  });
}
