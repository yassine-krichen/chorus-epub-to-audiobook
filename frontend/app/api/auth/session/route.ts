import { NextResponse } from "next/server";

import { readAuthCookies } from "@/lib/server/auth-cookies";
import type { SessionResponse } from "@/types/auth";

// The only way client code learns auth state: it never reads the httpOnly
// cookies itself. This checks cookie presence only, not token validity —
// a stale/expired access token still reports authenticated:true here, and
// gets refreshed or rejected on the next real backend call.
export async function GET(): Promise<NextResponse<SessionResponse>> {
  const { accessToken, email } = await readAuthCookies();
  return NextResponse.json({ authenticated: Boolean(accessToken), email: accessToken ? email : null });
}
