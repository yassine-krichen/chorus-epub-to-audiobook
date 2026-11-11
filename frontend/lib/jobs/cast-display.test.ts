import { describe, expect, it } from "vitest";

import {
  evidenceSummary,
  genderLabel,
  isReadByNarrator,
  reviewCastState,
  shouldPollCast,
  summarizeCast,
} from "@/lib/jobs/cast-display";
import type { CastCharacter, CastReport } from "@/types/job";

function character(overrides: Partial<CastCharacter> = {}): CastCharacter {
  return {
    name: "Nephis",
    lines: 10,
    gender: "f",
    confidence: 0.9,
    needs_review: false,
    voice: "af_heart",
    voice_source: "gender",
    sample_line: "We move at dawn.",
    layer_counts: {},
    top_evidence: [],
    ...overrides,
  };
}

function report(characters: CastCharacter[]): CastReport {
  return { voice_mode: "cast", narrator_voice: "am_michael", confidence_threshold: 0.6, characters };
}

describe("genderLabel", () => {
  it("says Not sure when the gender is unknown", () => {
    expect(genderLabel(null)).toBe("Not sure");
    expect(genderLabel("f")).toBe("Female");
    expect(genderLabel("m")).toBe("Male");
  });
});

describe("isReadByNarrator", () => {
  it("is true for an unresolved character", () => {
    const echo = character({ gender: null, voice: "am_michael", voice_source: "narrator" });
    expect(isReadByNarrator(echo, "am_michael")).toBe(true);
  });

  it("is true for a character the user sent to the narrator voice", () => {
    const sent = character({ voice: "am_michael", voice_source: "override" });
    expect(isReadByNarrator(sent, "am_michael")).toBe(true);
  });

  it("is false for a character with a voice of their own", () => {
    expect(isReadByNarrator(character(), "am_michael")).toBe(false);
  });
});

describe("evidenceSummary", () => {
  it("lists the strongest kind of clue first, with plurals", () => {
    expect(evidenceSummary({ title: 1, pronoun: 12 })).toBe("12 pronouns, 1 title");
  });

  it("says so when the text gave no clue", () => {
    expect(evidenceSummary({})).toBe("No clues in the text");
  });

  it("shows a user override as their own choice, without a count", () => {
    expect(evidenceSummary({ override: 1 })).toBe("your choice");
  });

  it("keeps an unknown evidence layer readable instead of dropping it", () => {
    expect(evidenceSummary({ mystery: 2 })).toBe("2 mystery");
  });
});

describe("summarizeCast", () => {
  it("counts speakers, characters of unknown gender and characters the narrator reads", () => {
    const cast = report([
      character(),
      character({ name: "Echo", gender: null, voice: "am_michael", voice_source: "narrator" }),
      character({ name: "Kai", gender: "m", voice: "am_michael", voice_source: "override" }),
    ]);

    expect(summarizeCast(cast)).toEqual({ speakers: 3, notSure: 1, readByNarrator: 2 });
  });
});

describe("reviewCastState", () => {
  it("is ready once the report is there", () => {
    expect(reviewCastState(report([character()]), true)).toBe("ready");
    expect(reviewCastState(report([character()]), false)).toBe("ready");
  });

  it("is loading until the first answer has come back", () => {
    expect(reviewCastState(undefined, false)).toBe("loading");
  });

  it("is missing when the answer came back empty, so the user is not left waiting forever", () => {
    expect(reviewCastState(null, true)).toBe("missing");
  });
});

describe("shouldPollCast", () => {
  it("polls while a job is working and the report is not there yet", () => {
    expect(shouldPollCast("running", false, 0)).toBe(true);
    expect(shouldPollCast("awaiting_cast_review", false, 0)).toBe(true);
  });

  it("stops once the report is loaded and the first chapter is done", () => {
    expect(shouldPollCast("running", true, 1)).toBe(false);
  });

  it("keeps refreshing a loaded report until the first chapter is done, for a resumed job", () => {
    expect(shouldPollCast("running", true, 0)).toBe(true);
  });

  it("stops polling a job that waits for review once the report is loaded", () => {
    expect(shouldPollCast("awaiting_cast_review", true, 0)).toBe(false);
  });

  it("does not poll for jobs that are queued or already over", () => {
    expect(shouldPollCast("queued", false, 0)).toBe(false);
    expect(shouldPollCast("succeeded", false, 0)).toBe(false);
    expect(shouldPollCast("failed", false, 0)).toBe(false);
  });
});
