/**
 * Client-side mirror of book2audio.api.jobs.filenames.validate_filename_template.
 * The backend remains the real enforcement layer (this was a real
 * header-injection security fix there) — this only exists for inline form
 * UX, so it must be kept in sync with filenames.py by hand.
 */

const PLACEHOLDER = /\{[^{}]*\}/g;
const ALLOWED = /^\{title\}$|^\{part\}$|^\{part:0([0-9]{1,2})d\}$/;

export const MAX_FILENAME_TEMPLATE_LENGTH = 200;
const MAX_PART_WIDTH = 10;

// Filesystem-unsafe characters across Windows/macOS/Linux, plus control chars,
// plus Unicode control (Cc), line/paragraph separators (Zl, Zp) and bidi
// controls, mirroring the backend. ZWNJ/ZWJ are allowed on purpose: Persian
// and emoji titles need them.
const UNSAFE_CHARS =
  /[<>:"/\\|?*\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]/;

export function validateFilenameTemplate(template: string): string | null {
  if (template.length > MAX_FILENAME_TEMPLATE_LENGTH) {
    return `Template cannot be longer than ${MAX_FILENAME_TEMPLATE_LENGTH} characters.`;
  }

  const placeholders = template.match(PLACEHOLDER) ?? [];
  for (const placeholder of placeholders) {
    const allowed = ALLOWED.exec(placeholder);
    if (!allowed) {
      return `Unsupported placeholder ${placeholder}. Allowed: {title}, {part}, {part:0Nd}`;
    }
    if (allowed[1] !== undefined && Number(allowed[1]) > MAX_PART_WIDTH) {
      return `Part width in ${placeholder} cannot exceed ${MAX_PART_WIDTH}.`;
    }
  }

  const literalText = template.replace(PLACEHOLDER, "");
  if (UNSAFE_CHARS.test(literalText)) {
    return "Template contains unsafe characters outside its placeholders (quotes, backslashes, path separators, or control characters are not allowed).";
  }
  if (literalText.includes("..")) {
    return "Template cannot contain '..' outside its placeholders.";
  }

  return null;
}

/** Renders a preview of the template for display only — not a security
 * boundary, the backend does the real substitution/sanitization. */
export function previewFilenameTemplate(template: string, title: string, part = 1): string {
  return template.replace(PLACEHOLDER, (match) => {
    const allowed = ALLOWED.exec(match);
    if (!allowed) return match;
    if (match === "{title}") return title || "book";
    if (match === "{part}") return String(part);
    const width = Number(allowed[1]);
    return String(part).padStart(width, "0");
  });
}
