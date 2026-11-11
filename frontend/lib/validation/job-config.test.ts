import { describe, expect, it } from "vitest";

import { jobConfigSchema, newJobFormSchema } from "@/lib/validation/job-config";

function validConfig() {
  return {
    voice_mode: "cast" as "cast" | "narrator_only",
    attributor: "heuristic" as "heuristic" | "hybrid",
    narrator_voice: null,
    voice_overrides: {},
    male_voice: null,
    female_voice: null,
    speed: 1.0,
    bitrate: "128k",
    chapter_limit: 0,
    start_chapter: null as number | null,
    batch_size: 1,
    chapter_gap: 0,
    music: false,
    music_volume: 0.5,
    music_preset: "ambient" as const,
    expressive: false,
    filename_template: "{title}",
    thumbnails: false,
  };
}

describe("jobConfigSchema", () => {
  it("accepts a fully populated valid config", () => {
    const result = jobConfigSchema.safeParse(validConfig());
    expect(result.success).toBe(true);
  });

  it("accepts nullable voice fields set to null", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      narrator_voice: null,
      male_voice: null,
      female_voice: null,
    });
    expect(result.success).toBe(true);
  });

  it("accepts voice fields set to a voice id string", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      narrator_voice: "en-US-voice-1",
    });
    expect(result.success).toBe(true);
  });

  it("accepts both voice modes", () => {
    for (const voice_mode of ["cast", "narrator_only"] as const) {
      expect(jobConfigSchema.safeParse({ ...validConfig(), voice_mode }).success).toBe(true);
    }
  });

  it("rejects an unknown voice mode", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), voice_mode: "choir" });
    expect(result.success).toBe(false);
  });

  it("accepts both attributors", () => {
    for (const attributor of ["heuristic", "hybrid"] as const) {
      expect(jobConfigSchema.safeParse({ ...validConfig(), attributor }).success).toBe(true);
    }
  });

  it("rejects an unknown attributor", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), attributor: "oracle" });
    expect(result.success).toBe(false);
  });

  it("defaults the attributor to heuristic when it is missing", () => {
    const config: Partial<ReturnType<typeof validConfig>> = validConfig();
    delete config.attributor;
    const result = jobConfigSchema.safeParse(config);
    expect(result.success).toBe(true);
    expect(result.success && result.data.attributor).toBe("heuristic");
  });

  it("rejects a music_preset outside the known enum", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      music_preset: "jazz",
    });
    expect(result.success).toBe(false);
  });

  it("rejects a filename_template that fails the injection allowlist", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      filename_template: 'a"; foo="bar{title}',
    });
    expect(result.success).toBe(false);
  });

  it("rejects an empty filename_template", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      filename_template: "",
    });
    expect(result.success).toBe(false);
  });

  it("rejects a speed below the allowed minimum of 0.5", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      speed: 0.1,
    });
    expect(result.success).toBe(false);
  });

  it("rejects a speed above the allowed maximum of 2.0", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      speed: 3.0,
    });
    expect(result.success).toBe(false);
  });

  it("rejects a negative batch_size", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      batch_size: -1,
    });
    expect(result.success).toBe(false);
  });

  it("accepts batch_size 0, which the backend treats as one file for the whole book", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      batch_size: 0,
    });
    expect(result.success).toBe(true);
  });

  it("accepts {chapter_title} when batch_size is 1 (one file per chapter)", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      batch_size: 1,
      filename_template: "{chapter:03d} - {chapter_title}",
    });
    expect(result.success).toBe(true);
  });

  it("rejects {chapter_title} when batch_size is not 1", () => {
    for (const batch_size of [0, 2, 10]) {
      const result = jobConfigSchema.safeParse({
        ...validConfig(),
        batch_size,
        filename_template: "{chapter_title}",
      });
      expect(result.success).toBe(false);
    }
  });

  it("rejects a non-integer batch_size", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      batch_size: 1.5,
    });
    expect(result.success).toBe(false);
  });

  it("rejects a negative chapter_limit", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      chapter_limit: -1,
    });
    expect(result.success).toBe(false);
  });

  it("accepts a null start_chapter, meaning start automatically", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), start_chapter: null });
    expect(result.success).toBe(true);
  });

  it("accepts start_chapter 0, meaning include front matter", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), start_chapter: 0 });
    expect(result.success).toBe(true);
  });

  it("accepts a positive start_chapter", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), start_chapter: 12 });
    expect(result.success).toBe(true);
  });

  it("rejects a negative start_chapter", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), start_chapter: -1 });
    expect(result.success).toBe(false);
  });

  it("rejects a fractional start_chapter", () => {
    const result = jobConfigSchema.safeParse({ ...validConfig(), start_chapter: 1.5 });
    expect(result.success).toBe(false);
  });

  it("rejects a music_volume outside the 0-1 range", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      music_volume: 1.5,
    });
    expect(result.success).toBe(false);
  });
});

describe("newJobFormSchema", () => {
  it("accepts a valid title paired with a valid config", () => {
    const result = newJobFormSchema.safeParse({
      title: "My Book",
      config: validConfig(),
    });
    expect(result.success).toBe(true);
  });

  it("rejects an empty title", () => {
    const result = newJobFormSchema.safeParse({
      title: "",
      config: validConfig(),
    });
    expect(result.success).toBe(false);
  });

  it("rejects a title longer than 255 characters", () => {
    const result = newJobFormSchema.safeParse({
      title: "a".repeat(256),
      config: validConfig(),
    });
    expect(result.success).toBe(false);
  });
});

describe("jobConfigSchema smarter thumbnails", () => {
  it("accepts smarter thumbnails together with thumbnails", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      thumbnails: true,
      thumbnails_enhanced: true,
    });
    expect(result.success).toBe(true);
  });

  it("rejects smarter thumbnails when thumbnails are off", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      thumbnails: false,
      thumbnails_enhanced: true,
    });
    expect(result.success).toBe(false);
    expect(!result.success && result.error.issues[0].path).toEqual(["thumbnails_enhanced"]);
  });

  it("defaults smarter thumbnails to off when the field is missing", () => {
    const result = jobConfigSchema.safeParse(validConfig());
    expect(result.success && result.data.thumbnails_enhanced).toBe(false);
  });

  it("defaults gender overrides to none when the field is missing", () => {
    const result = jobConfigSchema.safeParse(validConfig());
    expect(result.success && result.data.gender_overrides).toEqual({});
  });

  it("accepts gender overrides of m and f", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      gender_overrides: { Cassie: "f", Kai: "m" },
    });
    expect(result.success).toBe(true);
  });

  it("rejects a gender override that is not m or f", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      gender_overrides: { Cassie: "x" },
    });
    expect(result.success).toBe(false);
  });

  it("rejects a gender override with an over-long name", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      gender_overrides: { ["x".repeat(81)]: "f" },
    });
    expect(result.success).toBe(false);
  });

  it("rejects more than 100 gender overrides", () => {
    const many = Object.fromEntries(Array.from({ length: 101 }, (_, i) => [`Name ${i}`, "m"]));
    const result = jobConfigSchema.safeParse({ ...validConfig(), gender_overrides: many });
    expect(result.success).toBe(false);
  });
});
