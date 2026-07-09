/**
 * Coverage gate smoke test.
 *
 * Single test that imports `src/server.ts` and asserts the export is
 * a function. The point is to make the v8 coverage report include at
 * least one line of `src/`, so the 80% threshold in `vitest.config.ts`
 * has a meaningful baseline to enforce. T04+ adds real tests with
 * real assertions; this stays as a structural check.
 */

import { describe, expect, it } from "vitest";

import { app } from "../src/server.js";

describe("coverage gate", () => {
  it("exposes the Hono `app` as a callable", () => {
    expect(app).toBeDefined();
    expect(typeof app.fetch).toBe("function");
  });

  it("responds 200 on /health (covers server.ts at least one line)", async () => {
    const res = await app.fetch(new Request("http://localhost/health"));
    expect(res.status).toBe(200);
  });
});
