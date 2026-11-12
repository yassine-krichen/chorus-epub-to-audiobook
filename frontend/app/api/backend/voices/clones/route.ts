import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";

export async function GET(): Promise<NextResponse> {
  const response = await backendFetch("/voices/clones");
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const body = await request.json();
  const response = await backendFetch("/voices/clones", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
