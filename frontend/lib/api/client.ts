export interface ApiResult<T> {
  data: T | null;
  error: string | null;
}

interface FastApiErrorDetail {
  msg?: string;
  loc?: (string | number)[];
}

interface FastApiErrorBody {
  detail?: string | FastApiErrorDetail[];
}

/** Normalizes FastAPI's two 422/error shapes (string detail, or a list of
 * per-field validation errors) into a single human-readable message. */
function extractErrorMessage(body: unknown, fallback: string): string {
  const detail = (body as FastApiErrorBody | null)?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => entry.msg)
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  return fallback;
}

/** Fetches a same-origin `/api/*` BFF route and returns a `{data, error}`
 * result object. Components react to this, they never catch exceptions —
 * this is the codebase's established frontend error-handling convention. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    const response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });

    if (response.status === 204) {
      return { data: null, error: null };
    }

    const body = await response.json().catch(() => null);

    if (!response.ok) {
      return { data: null, error: extractErrorMessage(body, `Request failed (${response.status})`) };
    }

    return { data: body as T, error: null };
  } catch {
    return { data: null, error: "Network error — please check your connection and try again." };
  }
}
