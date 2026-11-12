import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/server/backend-client";

export async function POST(): Promise<NextResponse> {
  const response = await backendFetch("/voices/clones/upload-url", { method: "POST" });
  const payload = await response.json().catch(() => null);
  return NextResponse.json(payload, { status: response.status });
}
