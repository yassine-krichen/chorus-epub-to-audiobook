import { NextRequest, NextResponse } from "next/server";

function backendUrl(path: string): string {
  const base = process.env.BACKEND_API_URL;
  if (!base) {
    throw new Error("BACKEND_API_URL environment variable is not set");
  }
  return `${base.replace(/\/$/, "")}${path}`;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const body = await request.json();
  const response = await fetch(backendUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
