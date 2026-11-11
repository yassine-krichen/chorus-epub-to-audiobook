import { z } from "zod";

import { MUSIC_PRESETS } from "@/lib/constants/music-presets";
import { MAX_CHARACTER_NAME_LENGTH, MAX_CHARACTER_OVERRIDES } from "@/lib/jobs/character-overrides";
import {
  MAX_FILENAME_TEMPLATE_LENGTH,
  validateFilenameTemplate,
} from "@/lib/validation/filename-template";

/**
 * Client-side shape/UX validation for the new-job form. Only
 * voice_mode, attributor, gender_overrides, male_voice/female_voice, music_preset, filename_template, batch_size (>= 0),
 * thumbnails_enhanced-needs-thumbnails and
 * the {chapter_title}-needs-one-file-per-chapter rule are actually
 * enforced server-side (see book2audio.api.jobs.schemas.JobConfig) — the
 * numeric ranges below (speed, bitrate, chapter_gap) are a UX
 * convention only, not a real constraint the backend imposes.
 */
export const jobConfigSchema = z.object({
  voice_mode: z.enum(["cast", "narrator_only"]),
  attributor: z.enum(["heuristic", "hybrid"]).default("heuristic"),
  narrator_voice: z.string().nullable(),
  voice_overrides: z.record(z.string(), z.string()),
  gender_overrides: z
    .record(z.string().min(1).max(MAX_CHARACTER_NAME_LENGTH), z.enum(["m", "f"]))
    .refine((overrides) => Object.keys(overrides).length <= MAX_CHARACTER_OVERRIDES, {
      message: `At most ${MAX_CHARACTER_OVERRIDES} characters can be set`,
    })
    .default({}),
  character_aliases: z.record(z.string(), z.string()).default({}),
  cast_review: z.boolean().default(true),
  male_voice: z.string().nullable(),
  female_voice: z.string().nullable(),
  speed: z.number().min(0.5).max(2.0),
  bitrate: z.string().min(1),
  chapter_limit: z.number().int().min(0),
  start_chapter: z.number().int().min(0).nullable(),
  // 1 = one file per chapter, 0 = one file for the whole book (backend semantics).
  batch_size: z.number().int().min(0),
  chapter_gap: z.number().min(0),
  music: z.boolean(),
  music_volume: z.number().min(0).max(1),
  music_preset: z.enum(MUSIC_PRESETS),
  expressive: z.boolean(),
  filename_template: z
    .string()
    .min(1)
    .max(MAX_FILENAME_TEMPLATE_LENGTH)
    .refine((value) => validateFilenameTemplate(value) === null, {
      message: "Invalid filename template",
    }),
  thumbnails: z.boolean(),
  thumbnails_enhanced: z.boolean().default(false),
})
  .refine(
    (config) => validateFilenameTemplate(config.filename_template, config.batch_size) === null,
    { message: "Invalid filename template for this batch size", path: ["filename_template"] }
  )
  .refine((config) => !config.thumbnails_enhanced || config.thumbnails, {
    message: "Smarter thumbnails need chapter thumbnails to be on",
    path: ["thumbnails_enhanced"],
  });

export const newJobFormSchema = z.object({
  title: z.string().min(1, "Title is required").max(255),
  config: jobConfigSchema,
});

export type JobConfigInput = z.infer<typeof jobConfigSchema>;
