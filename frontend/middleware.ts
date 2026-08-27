import { NextRequest, NextResponse } from "next/server";

// UX-only redirect, NOT the real access-control boundary. This only checks
// whether the access-token cookie is *present* — it does not verify the JWT
// signature (JWT_SECRET is intentionally never shipped to this app/Vercel).
// A present-but-expired or forged cookie value still passes this check.
// The actual enforcement happens server-side in lib/server/backend-client.ts,
// which sends the token to the FastAPI backend and lets it verify/refresh.
const PROTECTED_PREFIXES = ["/dashboard", "/jobs", "/settings"];
const ACCESS_COOKIE = "b2a_access";

export function middleware(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
  if (!isProtected) {
    return NextResponse.next();
  }

  const hasAccessCookie = Boolean(request.cookies.get(ACCESS_COOKIE)?.value);
  if (!hasAccessCookie) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/jobs/:path*", "/settings/:path*"],
};
