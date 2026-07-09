import { describe, expect, it } from "vitest";

import { computeIdempotencyKey, type WebhookPayload } from "../src/queue/jobs.js";

describe("computeIdempotencyKey", () => {
  it("falls back to empty strings when optional payload fields are missing", () => {
    const payload = {
      delivery: "delivery-minimal",
      event: "pull_request",
    } as WebhookPayload;

    const key = computeIdempotencyKey(payload);
    expect(key).toMatch(/^[a-f0-9]{64}$/);
    expect(key).toBe(
      computeIdempotencyKey({
        delivery: "delivery-minimal",
        event: "pull_request",
      } as WebhookPayload),
    );
  });
});
