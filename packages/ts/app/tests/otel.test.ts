import { describe, expect, it } from "vitest";

import { initOtel } from "../src/otel.js";
import { metricsRouter, recordPrReview } from "../src/metrics.js";

describe("otel", () => {
  it("initOtel() does not throw", () => {
    expect(() => initOtel()).not.toThrow();
  });
});

describe("metrics", () => {
  it("metricsRouter returns 200 with pr_reviews_total after recordPrReview", async () => {
    recordPrReview("passed");

    const app = metricsRouter();
    const res = await app.request("/metrics");

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("pr_reviews_total");
    expect(body).toContain('result="passed"');
  });
});
