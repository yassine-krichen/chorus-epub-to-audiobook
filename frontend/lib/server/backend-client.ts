import "server-only";

import {
  clearAuthCookies,
  readAuthCookies,
  setAuthCookies,
  type TokenPair,
} from "@/lib/server/auth-cookies";

function backendUrl(path: string): string {
  const base = process.env.BACKEND_API_URL;
  if (!base) {
    throw new Error("BACKEND_API_URL environment variable is not set");
  }
  return `${base.replace(/\/$/, "")}${path}`;
}

interface TokenPairPayload {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

async function refreshTokens(refreshToken: string): Promise<TokenPair | null> {
  const response = await fetch(backendUrl("/auth/refresh"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json()) as TokenPairPayload;
  return {
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresIn: payload.expires_in,
  };
}

/**
 * Calls the FastAPI backend with the access-token cookie as a Bearer header.
 * On a 401, uses the refresh-token cookie to rotate both tokens and retries
 * the call once. If the refresh also fails, clears all auth cookies so the
 * client's next `/api/auth/session` check reports logged-out.
 *
 * Must be called from a Route Handler / Server Action — it mutates cookies,
 * which Next.js only allows outside of Server Component render.
 */
export async function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken, refreshToken, email } = await readAuthCookies();
  if (!accessToken) {
    return new Response(null, { status: 401 });
  }

  const doFetch = (token: string) =>
    fetch(backendUrl(path), {
      ...init,
      headers: {
        ...init.headers,
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
    });

  let response = await doFetch(accessToken);
  if (response.status !== 401) {
    return response;
  }

  if (!refreshToken) {
    await clearAuthCookies();
    return response;
  }

  const rotated = await refreshTokens(refreshToken);
  if (!rotated) {
    await clearAuthCookies();
    return response;
  }

  await setAuthCookies(rotated, email ?? "");
  response = await doFetch(rotated.accessToken);
  if (response.status === 401) {
    await clearAuthCookies();
  }
  return response;
}
