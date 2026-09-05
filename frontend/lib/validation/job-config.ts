import { z } from "zod";

import { MUSIC_PRESETS } from "@/lib/constants/music-presets";
import { validateFilenameTemplate } from "@/lib/validation/filename-template";

/**
 * Client-side shape/UX validation for the new-job form. Only
 * male_voice/female_voice, music_preset, and filename_template are actually
 * enforced server-side (see book2audio.api.jobs.schemas.JobConfig) — the
 * numeric ranges below (speed, bitrate, batch_size, chapter_gap) are a UX
 * convention only, not a real constraint the backend imposes.
 */
export const jobConfigSchema = z.object({
  narrator_voice: z.string().nullable(),
  voice_overrides: z.record(z.string(), z.string()),
  male_voice: z.string().nullable(),
  female_voice: z.string().nullable(),
  speed: z.number().min(0.5).max(2.0),
  bitrate: z.string().min(1),
  chapter_limit: z.number().int().min(0),
  start_chapter: z.number().int().min(1),
  batch_size: z.number().int().min(1),
  chapter_gap: z.number().min(0),
  music: z.boolean(),
  music_volume: z.number().min(0).max(1),
  music_preset: z.enum(MUSIC_PRESETS),
  expressive: z.boolean(),
  filename_template: z
    .string()
    .min(1)
    .refine((value) => validateFilenameTemplate(value) === null, {
      message: "Invalid filename template",
    }),
  thumbnails: z.boolean(),
});

export const newJobFormSchema = z.object({
  title: z.string().min(1, "Title is required").max(255),
  config: jobConfigSchema,
});

export type JobConfigInput = z.infer<typeof jobConfigSchema>;
