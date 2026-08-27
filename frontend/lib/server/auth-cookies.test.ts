import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// next/headers' cookies() only works inside a real Next.js request scope.
// We fake just the surface auth-cookies.ts actually calls: get/set/delete.
interface FakeCookie {
  value: string;
}

function createFakeCookieStore() {
  const cookies = new Map<string, FakeCookie>();
  return {
    get: vi.fn((name: string) => cookies.get(name)),
    set: vi.fn((name: string, value: string) => {
      cookies.set(name, { value });
    }),
    delete: vi.fn((name: string) => {
      cookies.delete(name);
    }),
  };
}

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

describe("auth-cookies", () => {
  let store: ReturnType<typeof createFakeCookieStore>;

  beforeEach(() => {
    store = createFakeCookieStore();
    cookiesMock.mockResolvedValue(store);
  });

  afterEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  it("writes access, refresh, and email cookies with httpOnly/lax/path=/ options", async () => {
    const { setAuthCookies } = await import("@/lib/server/auth-cookies");

    await setAuthCookies(
      { accessToken: "access-1", refreshToken: "refresh-1", expiresIn: 900 },
      "user@example.com"
    );

    expect(store.set).toHaveBeenCalledWith(
      "b2a_access",
      "access-1",
      expect.objectContaining({
        httpOnly: true,
        sameSite: "lax",
        path: "/",
        maxAge: 900,
      })
    );
    expect(store.set).toHaveBeenCalledWith(
      "b2a_refresh",
      "refresh-1",
      // Mirrors book2audio.api.auth.security.REFRESH_TOKEN_TTL (30 days),
      // not exposed via the API so this value is hand-synced.
      expect.objectContaining({ maxAge: 30 * 24 * 60 * 60 })
    );
    expect(store.set).toHaveBeenCalledWith(
      "b2a_email",
      "user@example.com",
      expect.objectContaining({ maxAge: 30 * 24 * 60 * 60 })
    );
  });

  it("marks cookies as non-secure outside production", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.resetModules();
    const { setAuthCookies } = await import("@/lib/server/auth-cookies");

    await setAuthCookies(
      { accessToken: "access-1", refreshToken: "refresh-1", expiresIn: 900 },
      "user@example.com"
    );

    expect(store.set).toHaveBeenCalledWith(
      "b2a_access",
      "access-1",
      expect.objectContaining({ secure: false })
    );
  });

  it("marks cookies as secure in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.resetModules();
    const { setAuthCookies } = await import("@/lib/server/auth-cookies");

    await setAuthCookies(
      { accessToken: "access-1", refreshToken: "refresh-1", expiresIn: 900 },
      "user@example.com"
    );

    expect(store.set).toHaveBeenCalledWith(
      "b2a_access",
      "access-1",
      expect.objectContaining({ secure: true })
    );
  });

  it("deletes all three auth cookies on clear", async () => {
    const { clearAuthCookies } = await import("@/lib/server/auth-cookies");

    await clearAuthCookies();

    expect(store.delete).toHaveBeenCalledWith("b2a_access");
    expect(store.delete).toHaveBeenCalledWith("b2a_refresh");
    expect(store.delete).toHaveBeenCalledWith("b2a_email");
  });

  it("reads back previously set tokens and email", async () => {
    const { setAuthCookies, readAuthCookies } = await import(
      "@/lib/server/auth-cookies"
    );

    await setAuthCookies(
      { accessToken: "access-1", refreshToken: "refresh-1", expiresIn: 900 },
      "user@example.com"
    );
    const result = await readAuthCookies();

    expect(result).toEqual({
      accessToken: "access-1",
      refreshToken: "refresh-1",
      email: "user@example.com",
    });
  });

  it("returns null fields when no auth cookies are set", async () => {
    const { readAuthCookies } = await import("@/lib/server/auth-cookies");

    const result = await readAuthCookies();

    expect(result).toEqual({
      accessToken: null,
      refreshToken: null,
      email: null,
    });
  });
});
