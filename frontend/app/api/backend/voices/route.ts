import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";

export async function GET(): Promise<NextResponse> {
  const response = await backendFetch("/voices");
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
