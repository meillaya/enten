import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createApp } from "../src/server.js";
import prOpenedFixture from "./fixtures/webhook/pr-opened.json" assert { type: "json" };

const DATABASE_URL =
  process.env.DATABASE_URL?.replace("postgresql+asyncpg://", "postgresql://") ??
  "postgresql://enten:enten@localhost:5433/enten";

describe("idempotency integration", () => {
  let sql: Awaited<ReturnType<typeof createApp>>["sql"];
  let app: ReturnType<typeof createApp>["app"];

  beforeAll(() => {
    const created = createApp({
      secret: "test-webhook-secret",
      databaseUrl: DATABASE_URL,
    });
    sql = created.sql;
    app = created.app;
  });

  afterAll(async () => {
    await sql.end();
  });

  it("two identical deliveries result in one job row", async () => {
    const delivery = randomUUID();
    const payload = {
      ...prOpenedFixture,
      number: 4242,
      pull_request: {
        ...prOpenedFixture.pull_request,
        number: 4242,
      },
    };
    const body = JSON.stringify(payload);

    const { Webhooks } = await import("@octokit/webhooks");
    const webhooks = new Webhooks({ secret: "test-webhook-secret" });
    const signature = await webhooks.sign(body);

    const headers = {
      "X-GitHub-Event": "pull_request",
      "X-GitHub-Delivery": delivery,
      "X-Hub-Signature-256": signature,
      "Content-Type": "application/json",
    };

    const res1 = await app.request("/webhook", { method: "POST", body, headers });
    expect(res1.status).toBe(202);

    const res2 = await app.request("/webhook", { method: "POST", body, headers });
    expect(res2.status).toBe(202);

    const json1 = await res1.json();
    const json2 = await res2.json();
    expect(json1.idempotency_key).toBe(json2.idempotency_key);

    const rows = await sql<{ count: string }[]>`
      SELECT count(*)::text as count FROM jobs WHERE idempotency_key = ${json1.idempotency_key}
    `;
    expect(rows[0]?.count).toBe("1");
  });
});
