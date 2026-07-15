import { createHash } from "node:crypto";
import postgres from "postgres";
import { z } from "zod";

import { newRunId } from "../logging.js";

export const SUPPORTED_PULL_REQUEST_ACTIONS = [
  "opened",
  "reopened",
  "synchronize",
  "ready_for_review",
] as const;

const TraceparentSchema = z
  .string()
  .regex(
    /^(?!ff)[0-9a-f]{2}-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$/,
  );

function maxCodePoints(schema: z.ZodString, maximum: number) {
  return schema.refine((value) => Array.from(value).length <= maximum, {
    message: `must contain at most ${maximum} Unicode code points`,
  });
}

export const PrJobEnvelopeSchema = z
  .object({
    version: z.literal("1"),
    installation_id: z.number().int().positive(),
    delivery_id: z.string().uuid(),
    repository: z
      .object({
        id: z.number().int().positive(),
        full_name: maxCodePoints(
          z.string().regex(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$(?![\s\S])/),
          255,
        ),
      })
      .strict(),
    pull_request: z
      .object({
        number: z.number().int().positive(),
        head_sha: z.string().regex(/^[0-9a-f]{40,64}$/),
      })
      .strict(),
    action: z.enum(SUPPORTED_PULL_REQUEST_ACTIONS),
    actor: z
      .object({
        id: z.number().int().positive(),
        login: maxCodePoints(z.string().min(1), 255),
      })
      .strict(),
    review_contract: z.string().regex(/^[0-9a-f]{64}$/),
    traceparent: TraceparentSchema,
    tracestate: maxCodePoints(z.string().min(1), 512).optional(),
  })
  .strict();

export type PrJobEnvelope = z.infer<typeof PrJobEnvelopeSchema>;

export type EnqueueResult = {
  readonly runId: string;
  readonly idempotencyKey: string;
  readonly inserted: boolean;
};

export interface JobEnqueuer {
  enqueue(envelope: PrJobEnvelope): Promise<EnqueueResult>;
}

export function computeIdempotencyKey(envelope: PrJobEnvelope): string {
  const identity = [
    envelope.repository.id,
    envelope.pull_request.number,
    envelope.pull_request.head_sha,
    envelope.review_contract,
  ].join("|");
  return createHash("sha256").update(identity).digest("hex");
}

const REVIEW_CONTRACT_DOMAIN = "enten-review-contract-v1\0";

export function computeReviewContract(command: string, image: string): string {
  const payload = JSON.stringify({ command, image });
  return createHash("sha256")
    .update(REVIEW_CONTRACT_DOMAIN)
    .update(payload)
    .digest("hex");
}

type PersistedJob = {
  readonly run_id: string;
  readonly idempotency_key: string;
  readonly inserted: boolean;
};

export class EnqueueInvariantError extends Error {
  override readonly name = "EnqueueInvariantError";

  constructor(readonly idempotencyKey: string) {
    super(`enqueue returned no row for ${idempotencyKey}`);
  }
}

export class JobsQueue implements JobEnqueuer {
  constructor(private readonly sql: postgres.Sql) {}

  async enqueue(envelope: PrJobEnvelope): Promise<EnqueueResult> {
    const candidateRunId = newRunId();
    const idempotencyKey = computeIdempotencyKey(envelope);
    const traceId = envelope.traceparent.slice(3, 35);

    const rows = await this.sql<PersistedJob[]>`
      INSERT INTO jobs (run_id, kind, state, idempotency_key, trace_id, payload)
      VALUES (
        ${candidateRunId},
        'pull_request',
        'queued',
        ${idempotencyKey},
        ${traceId},
        ${this.sql.json(envelope)}
      )
      ON CONFLICT (idempotency_key) DO UPDATE
      SET idempotency_key = EXCLUDED.idempotency_key
      RETURNING run_id, idempotency_key, (xmax = 0) AS inserted
    `;

    const persisted = rows[0];
    if (persisted === undefined) {
      throw new EnqueueInvariantError(idempotencyKey);
    }

    return {
      runId: persisted.run_id,
      idempotencyKey: persisted.idempotency_key,
      inserted: persisted.inserted,
    };
  }
}

export function createJobsQueue(options: {
  readonly sql: postgres.Sql;
}): JobEnqueuer {
  return new JobsQueue(options.sql);
}
