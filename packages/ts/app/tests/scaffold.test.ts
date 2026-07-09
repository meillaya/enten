import { describe, expect, it } from "vitest";

import { app } from "../src/server.js";

describe("scaffold", () => {
  it("responds 200 on /health", async () => {
    const res = await app.fetch(new Request("http://localhost/health"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { status: string; service: string };
    expect(body.status).toBe("ok");
    expect(body.service).toBe("enten-webhook");
  });
});
