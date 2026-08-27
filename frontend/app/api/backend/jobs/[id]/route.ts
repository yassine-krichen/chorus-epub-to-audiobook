import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";

interface RouteParams {
  params: Promise<{ id: string }>;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isValidJobId(id: string): boolean {
  return UUID_PATTERN.test(id);
}

export async function GET(_request: Request, { params }: RouteParams): Promise<NextResponse> {
  const { id } = await params;
  if (!isValidJobId(id)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const response = await backendFetch(`/jobs/${id}`);
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}

export async function DELETE(_request: Request, { params }: RouteParams): Promise<NextResponse> {
  const { id } = await params;
  if (!isValidJobId(id)) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const response = await backendFetch(`/jobs/${id}`, { method: "DELETE" });
  if (response.status === 204) {
    return new NextResponse(null, { status: 204 });
  }
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
