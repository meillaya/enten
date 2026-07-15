import { describe, expect, it } from "vitest";

import { ConfigSchema, parse, type Config } from "../src/config.js";

/**
 * Tests for the TS edge configuration parser. Mirrors the Python
 * `test_config.py` 1:1 so a behavioural drift between the two sides
 * trips both test suites.
 */

const VALID_ENV: Record<string, string> = {
  GITHUB_APP_ID: "123456",
  GITHUB_WEBHOOK_SECRET: "test-webhook-secret-0123456789abcdef",
  DATABASE_URL: "postgresql://enten:enten@localhost/enten",
};

describe("config.parse", () => {
  it("returns ok for a complete env", () => {
    const result = parse(VALID_ENV);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.GITHUB_APP_ID).toBe("123456");
      expect(result.value.GITHUB_WEBHOOK_SECRET).toBe(
        "test-webhook-secret-0123456789abcdef",
      );
      expect(result.value.DATABASE_URL).toBe(
        "postgresql://enten:enten@localhost/enten",
      );
    }
  });

  it("returns ok: false when a required field is missing", () => {
    const result = parse({});
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(typeof result.error).toBe("string");
      expect(result.error.length).toBeGreaterThan(0);
    }
  });

  it("rejects a webhook secret shorter than 32 characters", () => {
    // Given
    const environment = {
      ...VALID_ENV,
      GITHUB_WEBHOOK_SECRET: "too-short",
    };

    // When
    const result = parse(environment);

    // Then
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("GITHUB_WEBHOOK_SECRET");
    }
  });

  it("applies the documented defaults for optional fields", () => {
    const result = parse(VALID_ENV);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.PORT).toBe("3000");
      expect(result.value.OTEL_ENDPOINT).toBe("http://localhost:4317");
      expect(result.value.METRICS_PORT).toBe("9091");
      expect(result.value.GITHUB_APP_SLUG).toBe("enten-bot");
      expect(result.value.SANDBOX_DOCKER_IMAGE).toBe(
        "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28",
      );
      expect(result.value.VERIFICATION_COMMAND).toBe(
        "python -m compileall -q .",
      );
    }
  });

  it("keeps PORT as a string and does not coerce to a number", () => {
    const result = parse(VALID_ENV);
    expect(result.ok).toBe(true);
    if (result.ok) {
      // The plan is explicit: PORT is `z.string()`, never a number.
      // If a future edit flips this to `z.coerce.number()`, this
      // test fails immediately and the listener still does its own
      // parseInt — the boot path stays consistent.
      expect(typeof result.value.PORT).toBe("string");
    }
  });

  it("respects an explicit PORT override", () => {
    const result = parse({ ...VALID_ENV, PORT: "8080" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.PORT).toBe("8080");
    }
  });

  it("reports the failing field path in the error", () => {
    const result = parse({ GITHUB_APP_ID: "1" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      // The error string should mention one of the missing required
      // fields so the operator doesn't have to scroll through zod's
      // nested issue tree at boot.
      expect(result.error).toMatch(/GITHUB_WEBHOOK_SECRET|DATABASE_URL/);
    }
  });

  it("ConfigSchema parses a hand-rolled valid object directly", () => {
    // Sanity check that the schema is exported and works on its own
    // (callers may want to skip the parse() wrapper and use the
    // schema with safeParse directly).
    const result = ConfigSchema.safeParse(VALID_ENV);
    expect(result.success).toBe(true);
    if (result.success) {
      const cfg: Config = result.data;
      expect(cfg.GITHUB_APP_ID).toBe("123456");
    }
  });
});
