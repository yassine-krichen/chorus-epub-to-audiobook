import { NextResponse } from "next/server";

import { clearAuthCookies, readAuthCookies } from "@/lib/server/auth-cookies";

function backendUrl(path: string): string {
  const base = process.env.BACKEND_API_URL;
  if (!base) {
    throw new Error("BACKEND_API_URL environment variable is not set");
  }
  return `${base.replace(/\/$/, "")}${path}`;
}

export async function POST(): Promise<NextResponse> {
  const { refreshToken } = await readAuthCookies();
  if (refreshToken) {
    // Best-effort revoke; cookies are cleared regardless of backend outcome
    // so the user is logged out client-side even if this call fails.
    await fetch(backendUrl("/auth/logout"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    }).catch(() => null);
  }
  await clearAuthCookies();
  return new NextResponse(null, { status: 204 });
}
