import "server-only";

import { cookies } from "next/headers";

/**
 * The three httpOnly cookies that carry auth state. Only Route Handlers set
 * or clear these — client JS never reads them (that's the point of httpOnly).
 * `b2a_email` exists only because the backend has no `/auth/me`: it's
 * captured from the login/register request body at auth time.
 */
const ACCESS_COOKIE = "b2a_access";
const REFRESH_COOKIE = "b2a_refresh";
const EMAIL_COOKIE = "b2a_email";

// Mirrors book2audio.api.auth.security.REFRESH_TOKEN_TTL (timedelta(days=30)).
// Not exposed via the API, so this constant must be kept in sync by hand.
const REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60;

const isProduction = process.env.NODE_ENV === "production";

function baseCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: isProduction,
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}

export interface TokenPair {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

export async function setAuthCookies(tokens: TokenPair, email: string): Promise<void> {
  const store = await cookies();
  store.set(ACCESS_COOKIE, tokens.accessToken, baseCookieOptions(tokens.expiresIn));
  store.set(REFRESH_COOKIE, tokens.refreshToken, baseCookieOptions(REFRESH_TOKEN_TTL_SECONDS));
  store.set(EMAIL_COOKIE, email, baseCookieOptions(REFRESH_TOKEN_TTL_SECONDS));
}

export async function clearAuthCookies(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
  store.delete(EMAIL_COOKIE);
}

export async function readAuthCookies(): Promise<{
  accessToken: string | null;
  refreshToken: string | null;
  email: string | null;
}> {
  const store = await cookies();
  return {
    accessToken: store.get(ACCESS_COOKIE)?.value ?? null,
    refreshToken: store.get(REFRESH_COOKIE)?.value ?? null,
    email: store.get(EMAIL_COOKIE)?.value ?? null,
  };
}
