import { Webhooks } from "@octokit/webhooks";
import type { Context } from "hono";
import { z } from "zod";

import { createLogger, newRunId, withRunId } from "../logging.js";
import {
  webhookRequestsTotal,
  webhookSignatureFailuresTotal,
} from "../metrics.js";
import {
  PrJobEnvelopeSchema,
  SUPPORTED_PULL_REQUEST_ACTIONS,
  type JobEnqueuer,
} from "../queue/jobs.js";
import type { RequestTracing } from "../tracing.js";
import { parseJson, readBoundedBody } from "./webhook_body.js";

const DEFAULT_MAX_WEBHOOK_BYTES = 1024 * 1024;

const PullRequestEventSchema = z.object({
  action: z.string().min(1),
  installation: z.object({ id: z.number().int().positive() }),
  repository: z.object({
    id: z.number().int().positive(),
    full_name: z.string().regex(/^[^/\s]+\/[^/\s]+$/),
    private: z.boolean(),
  }),
  pull_request: z.object({
    number: z.number().int().positive(),
    draft: z.boolean(),
    user: z.object({ login: z.string().min(1) }),
    head: z.object({
      sha: z.string().regex(/^[0-9a-f]{40,64}$/),
    }),
  }),
  sender: z.object({
    id: z.number().int().positive(),
    login: z.string().min(1),
  }),
});

const logger = createLogger("webhook.receiver");

export interface WebhookRouteOptions {
  readonly secret: string;
  readonly botSlug?: string;
  readonly queue: JobEnqueuer;
  readonly tracing: RequestTracing;
  readonly reviewContract: string;
  readonly maxWebhookBytes?: number;
  readonly verifySignature?: (
    body: string,
    signature: string,
  ) => Promise<boolean>;
}

function isSupportedAction(
  action: string,
): action is (typeof SUPPORTED_PULL_REQUEST_ACTIONS)[number] {
  return SUPPORTED_PULL_REQUEST_ACTIONS.some(
    (supported) => supported === action,
  );
}

export function createWebhookHandler(options: WebhookRouteOptions) {
  const webhooks = new Webhooks({ secret: options.secret });
  const maxBytes = options.maxWebhookBytes ?? DEFAULT_MAX_WEBHOOK_BYTES;
  const verifySignature =
    options.verifySignature ??
    ((body: string, signature: string) => webhooks.verify(body, signature));
  const botSlug = (options.botSlug ?? "enten-bot").toLowerCase();

  return async function webhookHandler(c: Context): Promise<Response> {
    return options.tracing.withServerSpan(async () => {
      const requestRunId = newRunId();
      return withRunId(async (log) => {
        webhookRequestsTotal.inc();

        const signature = c.req.header("X-Hub-Signature-256");
        if (signature === undefined) {
          webhookSignatureFailuresTotal.inc();
          return c.json({ error: "missing signature" }, 401);
        }

        const contentType = c.req.header("Content-Type");
        if (contentType?.split(";", 1)[0]?.trim() !== "application/json") {
          return c.json({ error: "unsupported content type" }, 415);
        }

        const read = await readBoundedBody(c.req.raw, maxBytes);
        if (read.kind === "too_large") {
          return c.json({ error: "payload too large" }, 413);
        }
        if (read.kind === "invalid_encoding") {
          return c.json({ error: "malformed body" }, 400);
        }

        let signatureValid: boolean;
        try {
          signatureValid = await verifySignature(read.body, signature);
        } catch (error: unknown) {
          if (error instanceof Error) {
            signatureValid = false;
          } else {
            throw error;
          }
        }
        if (!signatureValid) {
          webhookSignatureFailuresTotal.inc();
          return c.json({ error: "invalid signature" }, 401);
        }

        const parsedJson = parseJson(read.body);
        if (parsedJson.kind === "malformed") {
          return c.json({ error: "malformed body" }, 400);
        }

        const event = c.req.header("X-GitHub-Event");
        if (event === undefined) {
          return c.json({ error: "missing event" }, 400);
        }
        if (event !== "pull_request") {
          log.info({ event }, "signed event ignored");
          return c.json({ received: true, ignored: true }, 200);
        }

        const parsedEvent = PullRequestEventSchema.safeParse(parsedJson.value);
        if (!parsedEvent.success) {
          return c.json({ error: "invalid pull_request payload" }, 422);
        }
        const authorLogin =
          parsedEvent.data.pull_request.user.login.toLowerCase();
        if (
          parsedEvent.data.repository.private ||
          authorLogin === botSlug ||
          authorLogin === `${botSlug}[bot]` ||
          parsedEvent.data.pull_request.draft ||
          !isSupportedAction(parsedEvent.data.action)
        ) {
          return c.json({ received: true, ignored: true }, 200);
        }

        const delivery = c.req.header("X-GitHub-Delivery");
        if (delivery === undefined) {
          return c.json({ error: "missing delivery" }, 400);
        }

        try {
          const result = await options.tracing.withProducerSpan(
            async (produced) => {
              const envelopeInput = {
                version: "1",
                installation_id: parsedEvent.data.installation.id,
                delivery_id: delivery,
                repository: {
                  id: parsedEvent.data.repository.id,
                  full_name: parsedEvent.data.repository.full_name,
                },
                pull_request: {
                  number: parsedEvent.data.pull_request.number,
                  head_sha: parsedEvent.data.pull_request.head.sha,
                },
                action: parsedEvent.data.action,
                actor: {
                  id: parsedEvent.data.sender.id,
                  login: parsedEvent.data.sender.login,
                },
                review_contract: options.reviewContract,
                traceparent: produced.traceparent,
                ...(produced.tracestate === undefined
                  ? {}
                  : { tracestate: produced.tracestate }),
              };
              const envelope = PrJobEnvelopeSchema.parse(envelopeInput);
              return options.queue.enqueue(envelope);
            },
          );

          log.info(
            {
              delivery,
              idempotencyKey: result.idempotencyKey,
              inserted: result.inserted,
              runId: result.runId,
            },
            "job persisted",
          );
          return c.json(
            {
              received: true,
              idempotency_key: result.idempotencyKey,
              run_id: result.runId,
              inserted: result.inserted,
            },
            202,
          );
        } catch (error: unknown) {
          if (error instanceof Error) {
            log.error({ error }, "job persistence failed");
            return c.json({ error: "enqueue unavailable" }, 503);
          }
          throw error;
        }
      })(logger, requestRunId);
    });
  };
}
