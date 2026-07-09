import { Webhooks } from "@octokit/webhooks";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { computeIdempotencyKey, type JobsQueue, type WebhookPayload } from "../src/queue/jobs.js";
import { register } from "prom-client";
import { createApp } from "../src/server.js";
import prOpenedFixture from "./fixtures/webhook/pr-opened.json" assert { type: "json" };

const TEST_SECRET = "test-webhook-secret";

function createMockQueue(): JobsQueue {
  const enqueued: Array<{ payload: WebhookPayload; result: { runId: string; idempotencyKey: string; inserted: boolean } }> = [];

  return {
    enqueue: vi.fn(async (payload: WebhookPayload) => {
      const result = {
        runId: `pr_testrunid00${enqueued.length}`,
        idempotencyKey: computeIdempotencyKey(payload),
        inserted: true,
      };
      enqueued.push({ payload, result });
      return result;
    }),
    _enqueued: enqueued,
  } as unknown as JobsQueue;
}

async function signPayload(payload: string, secret = TEST_SECRET): Promise<string> {
  const webhooks = new Webhooks({ secret });
  return webhooks.sign(payload);
}

describe("POST /webhook", () => {
  let queue: JobsQueue;
  let app: ReturnType<typeof createApp>["app"];

  beforeEach(() => {
    register.resetMetrics();
    queue = createMockQueue();
    app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
    }).app;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns 202 for a valid signature", async () => {
    const payload = JSON.stringify(prOpenedFixture);
    const signature = await signPayload(payload);

    const res = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
      },
    });

    expect(res.status).toBe(202);
    const body = await res.json();
    expect(body.received).toBe(true);
    expect(body.idempotency_key).toBeDefined();
    expect(body.run_id).toBeDefined();
    expect(queue.enqueue).toHaveBeenCalledTimes(1);
  });

  it("returns 401 when the signature is missing", async () => {
    const payload = JSON.stringify(prOpenedFixture);

    const res = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-1",
        "Content-Type": "application/json",
      },
    });

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "missing signature" });
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("returns 401 when the signature is invalid", async () => {
    const payload = JSON.stringify(prOpenedFixture);

    const res = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
        "Content-Type": "application/json",
      },
    });

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "invalid signature" });
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("returns 202 for duplicate deliveries with the same idempotency key", async () => {
    const payload = JSON.stringify(prOpenedFixture);
    const signature = await signPayload(payload);

    const headers = {
      "X-GitHub-Event": "pull_request",
      "X-GitHub-Delivery": "delivery-dup",
      "X-Hub-Signature-256": signature,
      "Content-Type": "application/json",
    };

    const res1 = await app.request("/webhook", { method: "POST", body: payload, headers });
    expect(res1.status).toBe(202);

    const res2 = await app.request("/webhook", { method: "POST", body: payload, headers });
    expect(res2.status).toBe(202);

    const body1 = await res1.json();
    const body2 = await res2.json();
    expect(body1.idempotency_key).toBe(body2.idempotency_key);
  });

  it("returns 200 for unknown events without enqueueing", async () => {
    const payload = JSON.stringify({ action: "created", ref: "refs/heads/main" });
    const signature = await signPayload(payload);

    const res = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "delivery-push",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
      },
    });

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.received).toBe(true);
    expect(body.event).toBe("push");
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("returns 400 for a malformed body", async () => {
    const payload = "not-json";
    const signature = await signPayload(payload);

    const res = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-bad",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
      },
    });

    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "malformed body" });
    expect(queue.enqueue).not.toHaveBeenCalled();
  });
});

describe("GET /health", () => {
  it("returns 200 ok", async () => {
    const { app } = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue: createMockQueue(),
    });

    const res = await app.request("/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok", service: "enten-webhook" });
  });
});

describe("GET /metrics", () => {
  it("returns 200 text/plain Prometheus format", async () => {
    register.resetMetrics();
    const { app } = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue: createMockQueue(),
    });

    const res = await app.request("/metrics");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/plain");
    const body = await res.text();
    expect(body).toContain("# HELP enten_webhook_requests_total");
    expect(body).toContain("enten_webhook_requests_total 0");
  });
});
