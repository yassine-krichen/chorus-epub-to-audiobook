import { NextResponse } from "next/server";

export const INVALID_JSON_MESSAGE = "The request body must be valid JSON.";

export type JsonBody = { ok: true; body: unknown } | { ok: false; response: NextResponse };

/** Reads a request's JSON body. A body that is missing or not JSON is the caller's mistake: it
 * gets a 400 here, not an unhandled exception (a 500) from the route handler. */
export async function readJsonBody(request: Request): Promise<JsonBody> {
  try {
    return { ok: true, body: await request.json() };
  } catch {
    return {
      ok: false,
      response: NextResponse.json({ detail: INVALID_JSON_MESSAGE }, { status: 400 }),
    };
  }
}
