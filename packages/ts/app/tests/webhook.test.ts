import { Webhooks } from "@octokit/webhooks";
import { z } from "zod";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createApp } from "../src/server.js";
import type { ProducedTrace, RequestTracing } from "../src/tracing.js";
import prOpenedFixture from "./fixtures/webhook/pr-opened.json" assert { type: "json" };

const TEST_SECRET = "test-webhook-secret";
const DELIVERY_ID = "11111111-1111-4111-8111-111111111111";
const TRACEPARENT =
  "00-11111111111111111111111111111111-2222222222222222-01";
const TRACESTATE = "vendor=value";

const responseSchema = z.object({
  received: z.literal(true),
  run_id: z.string(),
  idempotency_key: z.string(),
  inserted: z.boolean(),
});

function createQueue() {
  const enqueue = vi.fn(async () => ({
    runId: "pr_persistedrunid",
    idempotencyKey: "a".repeat(64),
    inserted: true,
  }));
  return { enqueue };
}

class RecordingTracing implements RequestTracing {
  readonly serverSpans = vi.fn();
  readonly producerSpans = vi.fn();

  withServerSpan<T>(operation: () => Promise<T>): Promise<T> {
    this.serverSpans();
    return operation();
  }

  withProducerSpan<T>(
    operation: (trace: ProducedTrace) => Promise<T>,
  ): Promise<T> {
    this.producerSpans();
    return operation({
      traceId: "11111111111111111111111111111111",
      traceparent: TRACEPARENT,
      tracestate: TRACESTATE,
    });
  }
}

function createTracing(): RecordingTracing {
  return new RecordingTracing();
}

async function signPayload(payload: string): Promise<string> {
  return new Webhooks({ secret: TEST_SECRET }).sign(payload);
}

async function postSigned(
  app: ReturnType<typeof createApp>["app"],
  payload: string,
  headers: Readonly<Record<string, string>> = {},
): Promise<Response> {
  const signature = await signPayload(payload);
  return app.request("/webhook", {
    method: "POST",
    body: payload,
    headers: {
      "Content-Type": "application/json",
      "X-GitHub-Delivery": DELIVERY_ID,
      "X-GitHub-Event": "pull_request",
      "X-Hub-Signature-256": signature,
      ...headers,
    },
  });
}

describe("POST /webhook supported pull requests", () => {
  beforeEach(() => vi.restoreAllMocks());

  it.each(["opened", "reopened", "synchronize", "ready_for_review"])(
    "enqueues the strict versioned envelope when action is %s",
    async (action) => {
      // Given
      const queue = createQueue();
      const tracing = createTracing();
      const app = createApp({
        secret: TEST_SECRET,
        databaseUrl: "postgresql://unused",
        queue,
        tracing,
      }).app;
      const payload = JSON.stringify({ ...prOpenedFixture, action });

      // When
      const response = await postSigned(app, payload);

      // Then
      expect(response.status).toBe(202);
      expect(responseSchema.parse(await response.json())).toEqual({
        received: true,
        run_id: "pr_persistedrunid",
        idempotency_key: "a".repeat(64),
        inserted: true,
      });
      expect(queue.enqueue).toHaveBeenCalledWith({
        version: "1",
        installation_id: 987654,
        delivery_id: DELIVERY_ID,
        repository: { id: 1296269, full_name: "octocat/hello-world", private: false },
        pull_request: {
          number: 42,
          head_sha: "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000",
        },
        action,
        actor: { id: 1, login: "octocat" },
        review_contract: "0".repeat(64),
        traceparent: TRACEPARENT,
        tracestate: TRACESTATE,
      });
      expect(tracing.serverSpans).toHaveBeenCalledTimes(1);
      expect(tracing.producerSpans).toHaveBeenCalledTimes(1);
    },
  );
});

describe("POST /webhook ignored events", () => {
  it.each(["closed", "converted_to_draft", "edited", "labeled"])(
    "returns 200 without enqueue when the pull_request action is %s",
    async (action) => {
      // Given
      const queue = createQueue();
      const app = createApp({
        secret: TEST_SECRET,
        databaseUrl: "postgresql://unused",
        queue,
        tracing: createTracing(),
      }).app;

      // When
      const response = await postSigned(
        app,
        JSON.stringify({ ...prOpenedFixture, action }),
      );

      // Then
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ received: true, ignored: true });
      expect(queue.enqueue).not.toHaveBeenCalled();
    },
  );

  it("returns 200 without enqueue when the pull request is a draft", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify({
      ...prOpenedFixture,
      pull_request: { ...prOpenedFixture.pull_request, draft: true },
    });

    // When
    const response = await postSigned(app, payload);

    // Then
    expect(response.status).toBe(200);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it.each(["enten-bot", "ENTEN-BOT", "enten-bot[bot]", "Enten-Bot[bot]"])(
    "returns 200 without enqueue for bot login %s",
    async (login) => {
      // Given
      const queue = createQueue();
      const app = createApp({
        secret: TEST_SECRET,
        databaseUrl: "postgresql://unused",
        botSlug: "enten-bot",
        queue,
        tracing: createTracing(),
      }).app;
      const payload = JSON.stringify({
        ...prOpenedFixture,
        pull_request: {
          ...prOpenedFixture.pull_request,
          user: { ...prOpenedFixture.pull_request.user, login },
        },
      });

      // When
      const response = await postSigned(app, payload);

      // Then
      expect(response.status).toBe(200);
      expect(queue.enqueue).not.toHaveBeenCalled();
    },
  );

  it("enqueues a private repository event", async () => {
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify({
      ...prOpenedFixture,
      repository: { ...prOpenedFixture.repository, private: true },
    });

    const response = await postSigned(app, payload);

    expect(response.status).toBe(202);
    expect(queue.enqueue).toHaveBeenCalledWith(
      expect.objectContaining({
        repository: { id: 1296269, full_name: "octocat/hello-world", private: true },
      }),
    );
  });

  it("returns 200 without enqueue for every other signed event", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify({ action: "submitted" });

    // When
    const response = await postSigned(app, payload, {
      "X-GitHub-Event": "pull_request_review",
    });

    // Then
    expect(response.status).toBe(200);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });
});

describe("POST /webhook rejected input", () => {
  it("rejects a missing signature before reading or enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: JSON.stringify(prOpenedFixture),
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": DELIVERY_ID,
        "X-GitHub-Event": "pull_request",
      },
    });

    // Then
    expect(response.status).toBe(401);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects an invalid signature without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: JSON.stringify(prOpenedFixture),
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": DELIVERY_ID,
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": `sha256=${"0".repeat(64)}`,
      },
    });

    // Then
    expect(response.status).toBe(401);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects a malformed supported-event schema without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const malformed = JSON.stringify({
      ...prOpenedFixture,
      pull_request: { ...prOpenedFixture.pull_request, head: { sha: 42 } },
    });

    // When
    const response = await postSigned(app, malformed);

    // Then
    expect(response.status).toBe(422);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects a signed body larger than the configured byte cap", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      maxWebhookBytes: 128,
      queue,
      tracing: createTracing(),
    }).app;
    const oversized = JSON.stringify({
      ...prOpenedFixture,
      padding: "x".repeat(512),
    });

    // When
    const response = await postSigned(app, oversized);

    // Then
    expect(response.status).toBe(413);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects an unsupported content type without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify(prOpenedFixture);
    const signature = await signPayload(payload);

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "Content-Type": "text/plain",
        "X-GitHub-Delivery": DELIVERY_ID,
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
      },
    });

    // Then
    expect(response.status).toBe(415);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects malformed signed JSON without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await postSigned(app, "{");

    // Then
    expect(response.status).toBe(400);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects an absent event header without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify(prOpenedFixture);
    const signature = await signPayload(payload);

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": DELIVERY_ID,
        "X-Hub-Signature-256": signature,
      },
    });

    // Then
    expect(response.status).toBe(400);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects an absent delivery header without enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;
    const payload = JSON.stringify(prOpenedFixture);
    const signature = await signPayload(payload);

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: payload,
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
      },
    });

    // Then
    expect(response.status).toBe(400);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects invalid UTF-8 without attempting enqueue", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await app.request("/webhook", {
      method: "POST",
      body: new Uint8Array([0xc3, 0x28]),
      headers: {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": DELIVERY_ID,
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": `sha256=${"0".repeat(64)}`,
      },
    });

    // Then
    expect(response.status).toBe(400);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rejects an oversized declared content length before enqueueing", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      maxWebhookBytes: 128,
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await app.request(
      new Request("http://localhost/webhook", {
        method: "POST",
        body: "{}",
        headers: {
          "Content-Length": "1024",
          "Content-Type": "application/json",
          "X-GitHub-Delivery": DELIVERY_ID,
          "X-GitHub-Event": "pull_request",
          "X-Hub-Signature-256": `sha256=${"0".repeat(64)}`,
        },
      }),
    );

    // Then
    expect(response.status).toBe(413);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("treats verifier failures as invalid signatures", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
      verifySignature: () =>
        Promise.reject(new Error("verification unavailable")),
    }).app;

    // When
    const response = await postSigned(app, JSON.stringify(prOpenedFixture));

    // Then
    expect(response.status).toBe(401);
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("returns 503 when persistence fails", async () => {
    // Given
    const queue = {
      enqueue: vi.fn(() => Promise.reject(new Error("database unavailable"))),
    };
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const response = await postSigned(app, JSON.stringify(prOpenedFixture));

    // Then
    expect(response.status).toBe(503);
    expect(queue.enqueue).toHaveBeenCalledTimes(1);
  });

  it("rethrows a verifier rejection that is not an Error", async () => {
    // Given
    const queue = createQueue();
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
      verifySignature: () => Promise.reject("verification unavailable"),
    }).app;

    // When
    const request = postSigned(app, JSON.stringify(prOpenedFixture));

    // Then
    await expect(request).rejects.toBe("verification unavailable");
    expect(queue.enqueue).not.toHaveBeenCalled();
  });

  it("rethrows a persistence rejection that is not an Error", async () => {
    // Given
    const queue = {
      enqueue: vi.fn(() => Promise.reject("database unavailable")),
    };
    const app = createApp({
      secret: TEST_SECRET,
      databaseUrl: "postgresql://unused",
      queue,
      tracing: createTracing(),
    }).app;

    // When
    const request = postSigned(app, JSON.stringify(prOpenedFixture));

    // Then
    await expect(request).rejects.toBe("database unavailable");
    expect(queue.enqueue).toHaveBeenCalledTimes(1);
  });
});
