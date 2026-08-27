import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAuthCookies,
  readAuthCookies,
  setAuthCookies,
} from "@/lib/server/auth-cookies";
import { backendFetch } from "@/lib/server/backend-client";

vi.mock("@/lib/server/auth-cookies", () => ({
  readAuthCookies: vi.fn(),
  setAuthCookies: vi.fn(),
  clearAuthCookies: vi.fn(),
}));

const readAuthCookiesMock = vi.mocked(readAuthCookies);
const setAuthCookiesMock = vi.mocked(setAuthCookies);
const clearAuthCookiesMock = vi.mocked(clearAuthCookies);

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("backendFetch", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("BACKEND_API_URL", "https://backend.example.com");
    fetchMock.mockReset();
    readAuthCookiesMock.mockReset();
    setAuthCookiesMock.mockReset();
    clearAuthCookiesMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("returns 401 without calling fetch when there is no access-token cookie", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: null,
      refreshToken: null,
      email: null,
    });

    const response = await backendFetch("/jobs");

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("proxies a successful call through with the access token as a Bearer header", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "valid-access",
      refreshToken: "valid-refresh",
      email: "user@example.com",
    });
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }, 200));

    const response = await backendFetch("/jobs", {
      method: "POST",
      headers: { "X-Custom": "yes" },
    });

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://backend.example.com/jobs");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      Authorization: "Bearer valid-access",
      "X-Custom": "yes",
    });
    expect(setAuthCookiesMock).not.toHaveBeenCalled();
    expect(clearAuthCookiesMock).not.toHaveBeenCalled();
  });

  it("refreshes the token and retries once on a 401, returning the retried response", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "expired-access",
      refreshToken: "valid-refresh",
      email: "user@example.com",
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401)) // original call
      .mockResolvedValueOnce(
        jsonResponse(
          {
            access_token: "rotated-access",
            refresh_token: "rotated-refresh",
            expires_in: 900,
          },
          200
        )
      ) // refresh call
      .mockResolvedValueOnce(jsonResponse({ ok: true }, 200)); // retried call

    const response = await backendFetch("/jobs");

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "https://backend.example.com/auth/refresh"
    );
    const retryInit = fetchMock.mock.calls[2][1];
    expect(retryInit.headers).toMatchObject({
      Authorization: "Bearer rotated-access",
    });
    expect(setAuthCookiesMock).toHaveBeenCalledTimes(1);
    expect(setAuthCookiesMock).toHaveBeenCalledWith(
      {
        accessToken: "rotated-access",
        refreshToken: "rotated-refresh",
        expiresIn: 900,
      },
      "user@example.com"
    );
    expect(clearAuthCookiesMock).not.toHaveBeenCalled();
  });

  it("clears cookies and returns the 401 without retrying when there is no refresh-token cookie", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "expired-access",
      refreshToken: null,
      email: "user@example.com",
    });
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401));

    const response = await backendFetch("/jobs");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(clearAuthCookiesMock).toHaveBeenCalledTimes(1);
    expect(setAuthCookiesMock).not.toHaveBeenCalled();
  });

  it("clears cookies and returns 401 without throwing when the refresh call itself fails", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "expired-access",
      refreshToken: "stale-refresh",
      email: "user@example.com",
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401)) // original call
      .mockResolvedValueOnce(jsonResponse({ detail: "invalid" }, 400)); // refresh fails

    const response = await backendFetch("/jobs");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(clearAuthCookiesMock).toHaveBeenCalledTimes(1);
    expect(setAuthCookiesMock).not.toHaveBeenCalled();
  });

  it("does not retry a second time when the retried request is also a 401 (single-retry guard)", async () => {
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "expired-access",
      refreshToken: "valid-refresh",
      email: "user@example.com",
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401)) // original call
      .mockResolvedValueOnce(
        jsonResponse(
          {
            access_token: "rotated-access",
            refresh_token: "rotated-refresh",
            expires_in: 900,
          },
          200
        )
      ) // refresh succeeds
      .mockResolvedValueOnce(
        jsonResponse({ detail: "still unauthorized" }, 401)
      ); // retry also 401

    const response = await backendFetch("/jobs");

    expect(response.status).toBe(401);
    // Exactly 3 fetch calls (original + refresh + one retry) — a second
    // refresh/retry cycle would push this to 4+ and indicate an infinite loop.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(clearAuthCookiesMock).toHaveBeenCalledTimes(1);
    expect(setAuthCookiesMock).toHaveBeenCalledTimes(1);
  });

  it("throws when BACKEND_API_URL is not configured", async () => {
    vi.stubEnv("BACKEND_API_URL", "");
    readAuthCookiesMock.mockResolvedValue({
      accessToken: "valid-access",
      refreshToken: "valid-refresh",
      email: "user@example.com",
    });

    await expect(backendFetch("/jobs")).rejects.toThrow(
      "BACKEND_API_URL environment variable is not set"
    );
  });
});
