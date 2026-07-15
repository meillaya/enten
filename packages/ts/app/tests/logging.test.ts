/**
 * Tests for the structured logging module.
 *
 * These pin the contract documented in `src/logging.ts`:
 *
 * - `createLogger(name)` emits one JSON line per event to stdout.
 * - `api_key` (and friends) are redacted at emission time.
 * - `newRunId()` returns 16-char ids matching `^pr_[a-z0-9]{12}$`,
 *   uniquely over 1000 calls.
 *
 * Mirrors `packages/python/enten/tests/test_logging.py` 1:1 so a
 * behavioural drift between the two sides trips both test suites.
 *
 * Capture strategy: we spy on `process.stdout.write` so the test sees
 * exactly the bytes pino emitted, in order, without fighting pino's
 * internal `pino.DestinationStream` contract. This avoids the
 * "asynchronous flush" race that bites tests using a custom Writable.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { createLogger, newRunId, pinoLogger, withRunId } from "../src/logging.js";

/** Pattern from the spec — used across both test modules. */
const RUN_ID_RE = /^pr_[a-z0-9]{12}$/;
const SECRET_KEYS = [
  "api_key",
  "token",
  "webhook_secret",
  "password",
  "private_key",
] as const;

/**
 * Install a stdout spy that records every write into `lines`. Returns
 * a cleanup function the test should call (or use in afterEach).
 */
function captureStdout(): { lines: string[]; restore: () => void } {
  const lines: string[] = [];
  const spy = vi.spyOn(process.stdout, "write").mockImplementation(
    ((chunk: string | Uint8Array): boolean => {
      const text =
        typeof chunk === "string" ? chunk : Buffer.from(chunk).toString("utf8");
      for (const line of text.split("\n")) {
        if (line.length > 0) lines.push(line);
      }
      return true;
    }) as typeof process.stdout.write,
  );
  return {
    lines,
    restore: (): void => {
      spy.mockRestore();
    },
  };
}

describe("createLogger", () => {
  const cleanups: Array<() => void> = [];
  afterEach(() => {
    while (cleanups.length > 0) cleanups.pop()!();
  });

  it("emits a single JSON line per log call with the run_id field", () => {
    const cap = captureStdout();
    cleanups.push(cap.restore);
    const logger = createLogger("test");
    logger.info({ run_id: "pr_abc" }, "hello");

    expect(cap.lines).toHaveLength(1);
    const line = JSON.parse(cap.lines[0]!);
    expect(line.msg).toBe("hello");
    expect(line.run_id).toBe("pr_abc");
    expect(line.service).toBe("enten-webhook");
    expect(line.module).toBe("test");
    expect(line.level).toBe("info");
  });

  it.each(SECRET_KEYS)("redacts top-level %s fields", (key) => {
    // Given
    const cap = captureStdout();
    cleanups.push(cap.restore);
    const logger = createLogger("redact");

    // When
    logger.info({ [key]: "secret-value" }, "login");

    // Then
    expect(cap.lines).toHaveLength(1);
    const line = JSON.parse(cap.lines[0]!);
    expect(line[key]).toBe("[REDACTED]");
    expect(line.msg).toBe("login");
  });

  it.each(SECRET_KEYS)("redacts nested %s fields", (key) => {
    // Given
    const cap = captureStdout();
    cleanups.push(cap.restore);
    const logger = createLogger("redact");

    // When
    logger.info({ settings: { [key]: "secret-value" } }, "login");

    // Then
    expect(cap.lines).toHaveLength(1);
    const line = JSON.parse(cap.lines[0]!);
    expect(line.settings[key]).toBe("[REDACTED]");
    expect(line.msg).toBe("login");
  });
});

describe("newRunId", () => {
  it("returns ids matching the canonical 16-char pattern", () => {
    for (let i = 0; i < 100; i += 1) {
      const id = newRunId();
      expect(id).toMatch(RUN_ID_RE);
    }
  });

  it("returns 1000 distinct ids", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 1000; i += 1) ids.add(newRunId());
    expect(ids.size).toBe(1000);
  });
});

describe("pinoLogger / withRunId", () => {
  const cleanups: Array<() => void> = [];
  afterEach(() => {
    while (cleanups.length > 0) cleanups.pop()!();
  });

  it("exposes createLogger under the pinoLogger alias", () => {
    expect(pinoLogger).toBe(createLogger);
  });

  it("withRunId binds run_id onto the child logger for the call lifetime", async () => {
    const cap = captureStdout();
    cleanups.push(cap.restore);
    const parent = createLogger("wrap");
    const wrapped = withRunId(async (child, runId): Promise<string> => {
      child.info("first");
      child.info("second");
      return runId;
    });
    const result = await wrapped(parent, "pr_testabc1234");

    expect(result).toBe("pr_testabc1234");
    expect(cap.lines).toHaveLength(2);
    const first = JSON.parse(cap.lines[0]!);
    const second = JSON.parse(cap.lines[1]!);
    expect(first.run_id).toBe("pr_testabc1234");
    expect(second.run_id).toBe("pr_testabc1234");
  });
});
