import { describe, expect, it } from "vitest";

import { INVALID_JSON_MESSAGE, readJsonBody } from "@/lib/server/json-body";

function post(body: string | null): Request {
  return new Request("http://localhost/api", { method: "POST", body });
}

describe("readJsonBody", () => {
  it("returns the parsed body", async () => {
    const result = await readJsonBody(post(JSON.stringify({ size: 5 })));

    expect(result).toEqual({ ok: true, body: { size: 5 } });
  });

  it("answers 400, not an exception, for a body that is not JSON", async () => {
    const result = await readJsonBody(post("{not json"));

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.response.status).toBe(400);
      expect(await result.response.json()).toEqual({ detail: INVALID_JSON_MESSAGE });
    }
  });

  it("answers 400 for an empty body", async () => {
    const result = await readJsonBody(post(null));

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.response.status).toBe(400);
  });
});
