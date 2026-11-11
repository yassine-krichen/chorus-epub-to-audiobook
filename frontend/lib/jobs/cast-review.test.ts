import { describe, expect, it } from "vitest";

import {
  buildReviewRequest,
  canBeReplaced,
  changeCount,
  draftFor,
  formatDeadline,
  mergeTargets,
  treatAsToValue,
  updateDraft,
  valueToTreatAs,
  type DraftMap,
} from "@/lib/jobs/cast-review";
import type { CastCharacter, CastReport } from "@/types/job";

function character(name: string, overrides: Partial<CastCharacter> = {}): CastCharacter {
  return {
    name,
    lines: 5,
    gender: null,
    confidence: 0,
    needs_review: true,
    voice: "am_michael",
    voice_source: "narrator",
    sample_line: "Hm.",
    layer_counts: {},
    top_evidence: [],
    ...overrides,
  };
}

const report: CastReport = {
  voice_mode: "cast",
  narrator_voice: "am_michael",
  confidence_threshold: 0.6,
  characters: [
    character("Echo"),
    character("Rain"),
    character("Sunny", { gender: "m", voice: "am_adam", voice_source: "gender" }),
  ],
};

describe("updateDraft", () => {
  it("records a decision for one character", () => {
    const drafts = updateDraft({}, "Echo", { gender: "f" });

    expect(drafts).toEqual({ Echo: { gender: "f", voice: null, treatAs: { kind: "own" } } });
  });

  it("keeps earlier choices for the same character", () => {
    const first = updateDraft({}, "Echo", { gender: "f" });

    const second = updateDraft(first, "Echo", { voice: "af_sky" });

    expect(draftFor(second, "Echo")).toMatchObject({ gender: "f", voice: "af_sky" });
  });

  it("forgets a character once every choice is back to as detected", () => {
    const drafts = updateDraft(updateDraft({}, "Echo", { gender: "f" }), "Echo", { gender: null });

    expect(drafts).toEqual({});
  });

  it("does not change the map it was given", () => {
    const before: DraftMap = {};

    updateDraft(before, "Echo", { gender: "f" });

    expect(before).toEqual({});
  });
});

describe("buildReviewRequest", () => {
  it("sends nothing when the user changed nothing", () => {
    expect(buildReviewRequest(report, {})).toEqual({ characters: [] });
  });

  it("sends a gender and a voice for a character the user fixed", () => {
    const drafts = updateDraft({}, "Echo", { gender: "f", voice: "af_sky" });

    expect(buildReviewRequest(report, drafts)).toEqual({
      characters: [{ name: "Echo", gender: "f", voice: "af_sky" }],
    });
  });

  it("leaves out a gender that is the one already detected", () => {
    const drafts = updateDraft({}, "Sunny", { gender: "m" });

    expect(buildReviewRequest(report, drafts)).toEqual({ characters: [] });
  });

  it("sends a merge as the target alone, without any gender or voice", () => {
    const drafts = updateDraft({}, "Rain", {
      gender: "f",
      voice: "af_sky",
      treatAs: { kind: "merge", into: "Echo" },
    });

    expect(buildReviewRequest(report, drafts)).toEqual({
      characters: [{ name: "Rain", merge_into: "Echo" }],
    });
  });

  it("sends a hand-off to the narrator without any gender or voice", () => {
    const drafts = updateDraft({}, "Rain", { voice: "af_sky", treatAs: { kind: "narrator" } });

    expect(buildReviewRequest(report, drafts)).toEqual({
      characters: [{ name: "Rain", read_by_narrator: true }],
    });
  });

  it("lists characters in the order of the report", () => {
    let drafts: DraftMap = {};
    drafts = updateDraft(drafts, "Sunny", { voice: "am_eric" });
    drafts = updateDraft(drafts, "Echo", { gender: "f" });

    const names = buildReviewRequest(report, drafts).characters.map((edit) => edit.name);

    expect(names).toEqual(["Echo", "Sunny"]);
  });

  it("skips a merge into a character that was itself sent away, so the server sees no chain", () => {
    let drafts: DraftMap = {};
    drafts = updateDraft(drafts, "Rain", { treatAs: { kind: "merge", into: "Echo" } });
    drafts = updateDraft(drafts, "Echo", { treatAs: { kind: "narrator" } });

    expect(buildReviewRequest(report, drafts)).toEqual({
      characters: [{ name: "Echo", read_by_narrator: true }],
    });
  });
});

describe("changeCount", () => {
  it("counts the characters that will be sent", () => {
    const drafts = updateDraft(updateDraft({}, "Echo", { gender: "f" }), "Sunny", { gender: "m" });

    expect(changeCount(report, drafts)).toBe(1);
  });
});

describe("mergeTargets", () => {
  it("offers every other character", () => {
    expect(mergeTargets(report, {}, "Rain")).toEqual(["Echo", "Sunny"]);
  });

  it("leaves out characters that are themselves merged away", () => {
    const drafts = updateDraft({}, "Echo", { treatAs: { kind: "merge", into: "Sunny" } });

    expect(mergeTargets(report, drafts, "Rain")).toEqual(["Sunny"]);
  });

  it("offers nothing to a character that others were merged into", () => {
    const drafts = updateDraft({}, "Rain", { treatAs: { kind: "merge", into: "Echo" } });

    expect(mergeTargets(report, drafts, "Echo")).toEqual([]);
  });
});

describe("canBeReplaced", () => {
  it("is false for a character others were merged into", () => {
    const drafts = updateDraft({}, "Rain", { treatAs: { kind: "merge", into: "Echo" } });

    expect(canBeReplaced(drafts, "Echo")).toBe(false);
    expect(canBeReplaced(drafts, "Sunny")).toBe(true);
  });
});

describe("treatAs values", () => {
  it("round-trips the three choices", () => {
    for (const treatAs of [
      { kind: "own" },
      { kind: "narrator" },
      { kind: "merge", into: "Echo: the 1st" },
    ] as const) {
      expect(valueToTreatAs(treatAsToValue(treatAs))).toEqual(treatAs);
    }
  });

  it("reads an unknown value as the character's own voice", () => {
    expect(valueToTreatAs("???")).toEqual({ kind: "own" });
  });
});

describe("formatDeadline", () => {
  it("is empty without a date or with a bad one", () => {
    expect(formatDeadline(null)).toBe("");
    expect(formatDeadline(undefined)).toBe("");
    expect(formatDeadline("not a date")).toBe("");
  });

  it("formats a real date", () => {
    expect(formatDeadline("2026-11-13T14:05:00Z", "en-GB").length).toBeGreaterThan(0);
  });
});
