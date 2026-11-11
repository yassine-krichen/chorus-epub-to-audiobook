import { apiFetch } from "@/lib/api/client";
import type {
  BookInspection,
  CastReport,
  CastReviewRequest,
  CreateJobResponse,
  JobConfig,
  JobDetail,
  JobListResponse,
  UploadUrlResponse,
} from "@/types/job";

export async function fetchJobs(page: number, pageSize = 20) {
  return apiFetch<JobListResponse>(`/api/backend/jobs?page=${page}&page_size=${pageSize}`);
}

export async function fetchJob(jobId: string) {
  return apiFetch<JobDetail>(`/api/backend/jobs/${jobId}`);
}

export async function fetchJobCast(jobId: string) {
  return apiFetch<CastReport>(`/api/backend/jobs/${jobId}/cast`);
}

export async function confirmCastReview(jobId: string, review: CastReviewRequest) {
  return apiFetch<CreateJobResponse>(`/api/backend/jobs/${jobId}/cast-review`, {
    method: "POST",
    body: JSON.stringify(review),
  });
}

export async function cancelJob(jobId: string) {
  return apiFetch<null>(`/api/backend/jobs/${jobId}`, { method: "DELETE" });
}

export async function requestUploadUrl(filename: string) {
  return apiFetch<UploadUrlResponse>("/api/backend/jobs/upload-url", {
    method: "POST",
    body: JSON.stringify({ filename }),
  });
}

export async function inspectEpub(epubObjectKey: string) {
  return apiFetch<BookInspection>("/api/backend/jobs/inspect", {
    method: "POST",
    body: JSON.stringify({ epub_object_key: epubObjectKey }),
  });
}

export async function createJob(epubObjectKey: string, title: string, config: JobConfig) {
  return apiFetch<CreateJobResponse>("/api/backend/jobs", {
    method: "POST",
    body: JSON.stringify({ epub_object_key: epubObjectKey, title, config }),
  });
}
