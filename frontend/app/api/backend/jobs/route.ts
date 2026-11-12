import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";
import { readJsonBody } from "@/lib/server/json-body";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const search = request.nextUrl.search;
  const response = await backendFetch(`/jobs${search}`);
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const parsed = await readJsonBody(request);
  if (!parsed.ok) return parsed.response;
  const response = await backendFetch("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed.body),
  });
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
