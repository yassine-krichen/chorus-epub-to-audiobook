export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";

export const ACTIVE_JOB_STATUSES: readonly JobStatus[] = ["queued", "running"];

export interface JobConfig {
  narrator_voice: string | null;
  voice_overrides: Record<string, string>;
  male_voice: string | null;
  female_voice: string | null;
  speed: number;
  bitrate: string;
  chapter_limit: number;
  batch_size: number;
  chapter_gap: number;
  music: boolean;
  music_volume: number;
  music_preset: string;
  expressive: boolean;
  filename_template: string | null;
  thumbnails: boolean;
}

/** Every JobConfig field has a server-side default, so a new job's config
 * only needs to send the fields the user actually changed from those
 * defaults — but the form always builds and sends a full JobConfig. */
export const DEFAULT_JOB_CONFIG: JobConfig = {
  narrator_voice: null,
  voice_overrides: {},
  male_voice: null,
  female_voice: null,
  speed: 1.0,
  bitrate: "64k",
  chapter_limit: 0,
  batch_size: 10,
  chapter_gap: 1.8,
  music: true,
  music_volume: 0.22,
  music_preset: "ambient",
  expressive: true,
  // Left blank server-side, download_url is never presigned for finished
  // parts (see book2audio.api.jobs.service._to_part_progress). Always default
  // this to a real template so downloads work.
  filename_template: "{title} - Part {part:02d}",
  thumbnails: false,
};

export interface ChapterProgress {
  index: number;
  title: string;
  status: string;
  thumbnail_status: "ready" | "failed" | null;
  thumbnail_url: string | null;
}

export interface PartProgress {
  part_number: number;
  first_index: number;
  last_index: number;
  r2_key: string;
  status: string;
  download_url: string | null;
}

export interface JobSummary {
  id: string;
  title: string;
  status: JobStatus;
  total_chapters: number | null;
  progress_percent: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobDetail extends JobSummary {
  epub_object_key: string;
  config: JobConfig;
  chapters: ChapterProgress[];
  parts: PartProgress[];
  error_message: string | null;
}

export interface JobListResponse {
  jobs: JobSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface UploadUrlResponse {
  upload_url: string;
  object_key: string;
}

export interface CreateJobResponse {
  job_id: string;
  status: string;
}
