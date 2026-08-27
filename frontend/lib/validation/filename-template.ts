/**
 * Client-side mirror of book2audio.api.jobs.filenames.validate_filename_template.
 * The backend remains the real enforcement layer (this was a real
 * header-injection security fix there) — this only exists for inline form
 * UX, so it must be kept in sync with filenames.py by hand.
 */

const PLACEHOLDER = /\{[^{}]*\}/g;
const ALLOWED = /^\{title\}$|^\{part\}$|^\{part:0(\d+)d\}$/;

// Filesystem-unsafe characters across Windows/macOS/Linux, plus control chars.
const UNSAFE_CHARS = /[<>:"/\\|?*\x00-\x1f]/;

export function validateFilenameTemplate(template: string): string | null {
  const placeholders = template.match(PLACEHOLDER) ?? [];
  for (const placeholder of placeholders) {
    if (!ALLOWED.test(placeholder)) {
      return `Unsupported placeholder ${placeholder}. Allowed: {title}, {part}, {part:0Nd}`;
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
