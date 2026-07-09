/**
 * Thin SQL abstraction for enqueueing jobs from the webhook receiver.
 *
 * T10 will replace this with a full queue implementation. For now it
 * only exposes `enqueue(payload)` which inserts a `queued` row into the
 * `jobs` table with an idempotency key and returns the generated run_id.
 */

import { createHash } from "node:crypto";
import postgres from "postgres";

import { newRunId } from "../logging.js";

export interface WebhookPayload {
  delivery: string;
  event: string;
  repository?: { full_name?: string };
  pull_request?: { number?: number; head?: { sha?: string } };
  [key: string]: unknown;
}

export interface EnqueueResult {
  runId: string;
  idempotencyKey: string;
  inserted: boolean;
}

export function computeIdempotencyKey(payload: WebhookPayload): string {
  const repo = payload.repository?.full_name ?? "";
  const prNumber = payload.pull_request?.number ?? 0;
  const headSha = payload.pull_request?.head?.sha ?? "";
  const raw = `${payload.delivery}|${repo}|${prNumber}|${headSha}`;
  return createHash("sha256").update(raw).digest("hex");
}

export interface JobsQueueOptions {
  sql: postgres.Sql;
}

export class JobsQueue {
  constructor(private readonly sql: postgres.Sql) {}

  async enqueue(payload: WebhookPayload): Promise<EnqueueResult> {
    const runId = newRunId();
    const idempotencyKey = computeIdempotencyKey(payload);
    const kind = payload.event;

    const result = await this.sql<{ run_id: string; idempotency_key: string }[]>`
      INSERT INTO jobs (run_id, kind, state, idempotency_key, trace_id, payload)
      VALUES (
        ${runId},
        ${kind},
        'queued',
        ${idempotencyKey},
        NULL,
        ${this.sql.json(payload as never)}
      )
      ON CONFLICT (idempotency_key) DO NOTHING
      RETURNING run_id, idempotency_key
    `;

    return {
      runId,
      idempotencyKey,
      inserted: result.length > 0,
    };
  }
}

export function createJobsQueue(options: JobsQueueOptions): JobsQueue {
  return new JobsQueue(options.sql);
}
