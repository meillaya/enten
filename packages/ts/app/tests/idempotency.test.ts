import { createHash, randomUUID } from "node:crypto";
import { Webhooks } from "@octokit/webhooks";
import { z } from "zod";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { PrJobEnvelopeSchema } from "../src/queue/jobs.js";
import { initOtel, type OtelLifecycle } from "../src/otel.js";
import { createApp } from "../src/server.js";
import prOpenedFixture from "./fixtures/webhook/pr-opened.json" assert { type: "json" };

const DATABASE_URL =
  process.env.DATABASE_URL?.replace("postgresql+asyncpg://", "postgresql://") ??
  "postgresql://enten:enten@localhost:5433/enten";
const SECRET = "test-webhook-secret";
const responseSchema = z.object({
  run_id: z.string(),
  idempotency_key: z.string(),
  inserted: z.boolean(),
});

describe("idempotency and trace integration", () => {
  let created: ReturnType<typeof createApp>;
  let telemetry: OtelLifecycle;

  beforeAll(() => {
    const exporter = {
      export: (_spans: readonly unknown[], callback: (result: { code: number }) => void) =>
        callback({ code: 0 }),
      forceFlush: async () => undefined,
      shutdown: async () => undefined,
    };
    telemetry = initOtel(undefined, exporter, []);
    created = createApp({
      secret: SECRET,
      databaseUrl: DATABASE_URL,
    });
  });

  afterAll(async () => {
    await created.sql.end();
    await telemetry.shutdown();
  });

  it("returns the same persisted run and carrier for a repo/PR/SHA redelivery", async () => {
    // Given
    const headSha = createHash("sha256").update(randomUUID()).digest("hex");
    const firstDelivery = randomUUID();
    const secondDelivery = randomUUID();
    const payload = {
      ...prOpenedFixture,
      pull_request: {
        ...prOpenedFixture.pull_request,
        head: { ...prOpenedFixture.pull_request.head, sha: headSha },
      },
    };
    const body = JSON.stringify(payload);
    const signature = await new Webhooks({ secret: SECRET }).sign(body);
    const request = (delivery: string) =>
      created.app.request("/webhook", {
        method: "POST",
        body,
        headers: {
          "Content-Type": "application/json",
          "X-GitHub-Delivery": delivery,
          "X-GitHub-Event": "pull_request",
          "X-Hub-Signature-256": signature,
        },
      });

    // When
    const firstResponse = await request(firstDelivery);
    const secondResponse = await request(secondDelivery);

    // Then
    expect(firstResponse.status).toBe(202);
    expect(secondResponse.status).toBe(202);
    const first = responseSchema.parse(await firstResponse.json());
    const second = responseSchema.parse(await secondResponse.json());
    expect(first.run_id).toBe(second.run_id);
    expect(first.inserted).toBe(true);
    expect(second.inserted).toBe(false);

    const rows = await created.sql<
      { readonly run_id: string; readonly trace_id: string; readonly payload: unknown }[]
    >`
      SELECT run_id, trace_id, payload
      FROM jobs
      WHERE idempotency_key = ${first.idempotency_key}
    `;
    expect(rows).toHaveLength(1);
    expect(rows[0]?.run_id).toBe(first.run_id);
    const persistedEnvelope = PrJobEnvelopeSchema.parse(rows[0]?.payload);
    expect(rows[0]?.trace_id).toBe(persistedEnvelope.traceparent.slice(3, 35));
    expect(persistedEnvelope.delivery_id).toBe(firstDelivery);

    await created.sql`
      DELETE FROM jobs WHERE idempotency_key = ${first.idempotency_key}
    `;
  });
});
