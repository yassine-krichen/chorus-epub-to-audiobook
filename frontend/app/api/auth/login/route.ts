import { NextRequest, NextResponse } from "next/server";

import { setAuthCookies } from "@/lib/server/auth-cookies";

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

export async function POST(request: NextRequest): Promise<NextResponse> {
  const body = await request.json();
  const response = await fetch(backendUrl("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    return NextResponse.json(payload, { status: response.status });
  }

  const tokens = (await response.json()) as TokenPairPayload;
  // Login never returns an email in its response body — the request body is
  // the only place we have it, since there's no /auth/me to fetch it later.
  const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
  await setAuthCookies(
    {
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
      expiresIn: tokens.expires_in,
    },
    email
  );

  // Tokens never leave this handler — the client only learns auth state via
  // /api/auth/session.
  return NextResponse.json({ authenticated: true, email }, { status: 200 });
}
