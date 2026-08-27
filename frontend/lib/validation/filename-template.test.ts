import { describe, expect, it } from "vitest";

import {
  previewFilenameTemplate,
  validateFilenameTemplate,
} from "@/lib/validation/filename-template";

describe("validateFilenameTemplate", () => {
  it("accepts a template containing only {title}", () => {
    expect(validateFilenameTemplate("{title}")).toBeNull();
  });

  it("accepts a template containing only {part}", () => {
    expect(validateFilenameTemplate("{part}")).toBeNull();
  });

  it("accepts a zero-padded {part:0Nd} placeholder", () => {
    expect(validateFilenameTemplate("{part:02d}")).toBeNull();
    expect(validateFilenameTemplate("{part:03d}")).toBeNull();
  });

  it("accepts multiple allowed placeholders combined with safe literal text", () => {
    expect(
      validateFilenameTemplate("My Book - {title} - Part {part:02d}")
    ).toBeNull();
  });

  it("accepts an empty template (validator has no minimum-length rule; enforced separately by the job-config schema)", () => {
    expect(validateFilenameTemplate("")).toBeNull();
  });

  it("rejects an unknown placeholder token", () => {
    expect(validateFilenameTemplate("{unknown}")).not.toBeNull();
  });

  it("rejects an attribute-access-style placeholder trick", () => {
    expect(validateFilenameTemplate("{title.__class__}")).not.toBeNull();
  });

  it("rejects the second placeholder when only one of several is unsupported", () => {
    expect(validateFilenameTemplate("{title}_{bad}")).not.toBeNull();
  });

  it("rejects quote-breakout literal text (the actual backend Content-Disposition header-injection vulnerability)", () => {
    expect(validateFilenameTemplate('a"; foo="bar{title}')).not.toBeNull();
  });

  it("rejects CRLF injection in literal text", () => {
    expect(
      validateFilenameTemplate("a\r\nSet-Cookie: evil=1{title}")
    ).not.toBeNull();
  });

  it("rejects other control characters in literal text", () => {
    expect(validateFilenameTemplate("a\x07b{title}")).not.toBeNull();
  });

  it("rejects backslashes in literal text", () => {
    expect(validateFilenameTemplate("a\\b{title}")).not.toBeNull();
  });

  it("rejects forward slashes in literal text", () => {
    expect(validateFilenameTemplate("a/b{title}")).not.toBeNull();
  });

  it("rejects path traversal ('..') in literal text even without other unsafe characters", () => {
    expect(validateFilenameTemplate("..{part}")).not.toBeNull();
  });

  it("accepts literal text containing a single dot (only '..' is disallowed, not '.')", () => {
    expect(validateFilenameTemplate("v1.0 {title}")).toBeNull();
  });
});

describe("previewFilenameTemplate", () => {
  it("substitutes {title} with the given title", () => {
    expect(previewFilenameTemplate("{title}", "My Book")).toBe("My Book");
  });

  it("falls back to 'book' when title is empty", () => {
    expect(previewFilenameTemplate("{title}", "")).toBe("book");
  });

  it("substitutes {part} with the given part number", () => {
    expect(previewFilenameTemplate("{part}", "My Book", 3)).toBe("3");
  });

  it("zero-pads {part:0Nd} to the requested width", () => {
    expect(previewFilenameTemplate("{part:03d}", "My Book", 7)).toBe("007");
  });

  it("leaves an unsupported placeholder untouched (preview is not a security boundary)", () => {
    expect(previewFilenameTemplate("{unknown}", "My Book")).toBe("{unknown}");
  });
});
