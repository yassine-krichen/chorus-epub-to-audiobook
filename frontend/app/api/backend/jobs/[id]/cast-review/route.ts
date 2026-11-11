import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";

interface RouteParams {
  params: Promise<{ id: string }>;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request, { params }: RouteParams): Promise<NextResponse> {
  const { id } = await params;
  if (!UUID_PATTERN.test(id)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const body: unknown = await request.json().catch(() => null);
  if (body === null || typeof body !== "object") {
    return NextResponse.json({ detail: "Expected a JSON body" }, { status: 400 });
  }
  const response = await backendFetch(`/jobs/${id}/cast-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
