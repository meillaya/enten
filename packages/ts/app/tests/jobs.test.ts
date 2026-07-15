import { describe, expect, it, vi } from "vitest";

import {
  computeIdempotencyKey,
  createJobsQueue,
  EnqueueInvariantError,
  JobsQueue,
  PrJobEnvelopeSchema,
} from "../src/queue/jobs.js";

const envelope = PrJobEnvelopeSchema.parse({
  version: "1",
  installation_id: 987654,
  delivery_id: "11111111-1111-4111-8111-111111111111",
  repository: { id: 1296269, full_name: "octocat/hello-world" },
  pull_request: {
    number: 42,
    head_sha: "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
  },
  action: "opened",
  actor: { id: 1, login: "octocat" },
  traceparent: "00-11111111111111111111111111111111-2222222222222222-01",
  tracestate: "vendor=value",
});

describe("PrJobEnvelopeSchema", () => {
  it("rejects unknown fields so the worker contract cannot drift silently", () => {
    // Given
    const input = { ...envelope, unexpected: true };

    // When
    const result = PrJobEnvelopeSchema.safeParse(input);

    // Then
    expect(result.success).toBe(false);
  });
});

describe("computeIdempotencyKey", () => {
  it("deduplicates the same repo, PR, and head SHA across deliveries", () => {
    // Given
    const redelivery = {
      ...envelope,
      delivery_id: "22222222-2222-4222-8222-222222222222",
    };

    // When
    const originalKey = computeIdempotencyKey(envelope);
    const redeliveryKey = computeIdempotencyKey(redelivery);

    // Then
    expect(redeliveryKey).toBe(originalKey);
  });
});

describe("JobsQueue", () => {
  it("returns the persisted run ID when an enqueue conflicts", async () => {
    // Given
    const persistedRunId = "pr_existingpersisted";
    const sql = vi.fn(async () => [
      {
        run_id: persistedRunId,
        idempotency_key: computeIdempotencyKey(envelope),
        inserted: false,
      },
    ]);
    Object.assign(sql, { json: vi.fn((value) => value) });
    const queue = new JobsQueue(sql);

    // When
    const result = await queue.enqueue(envelope);

    // Then
    expect(result).toEqual({
      runId: persistedRunId,
      idempotencyKey: computeIdempotencyKey(envelope),
      inserted: false,
    });
  });

  it("raises a typed invariant error when the database returns no row", async () => {
    // Given
    const sql = vi.fn(async () => []);
    Object.assign(sql, { json: vi.fn((value) => value) });
    const queue = new JobsQueue(sql);

    // When
    const enqueue = queue.enqueue(envelope);

    // Then
    await expect(enqueue).rejects.toBeInstanceOf(EnqueueInvariantError);
  });

  it("creates a queue through the public factory", () => {
    // Given
    const sql = vi.fn(async () => []);
    Object.assign(sql, { json: vi.fn((value) => value) });

    // When
    const queue = createJobsQueue({ sql });

    // Then
    expect(queue).toBeInstanceOf(JobsQueue);
  });
});
