import { describe, expect, it } from "vitest";

import { jobConfigSchema, newJobFormSchema } from "@/lib/validation/job-config";

function validConfig() {
  return {
    narrator_voice: null,
    voice_overrides: {},
    male_voice: null,
    female_voice: null,
    speed: 1.0,
    bitrate: "128k",
    chapter_limit: 0,
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

  it("rejects a batch_size below 1", () => {
    const result = jobConfigSchema.safeParse({
      ...validConfig(),
      batch_size: 0,
    });
    expect(result.success).toBe(false);
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
