import { Webhooks } from "@octokit/webhooks";
import type { Context } from "hono";

import { createLogger, newRunId, withRunId } from "../logging.js";
import {
  webhookRequestsTotal,
  webhookSignatureFailuresTotal,
} from "../metrics.js";
import { type JobsQueue, type WebhookPayload } from "../queue/jobs.js";

const logger = createLogger("webhook.receiver");

export interface WebhookRouteOptions {
  secret: string;
  queue: JobsQueue;
}

export function createWebhookHandler(options: WebhookRouteOptions) {
  const webhooks = new Webhooks({ secret: options.secret });

  return async function webhookHandler(c: Context): Promise<Response> {
    const requestRunId = newRunId();
    return withRunId(async (log) => {
      webhookRequestsTotal.inc();

      const rawBody = await c.req.text();
      const signature = c.req.header("X-Hub-Signature-256");
      const event = c.req.header("X-GitHub-Event") ?? "unknown";
      const delivery = c.req.header("X-GitHub-Delivery") ?? "unknown";

      log.info({ event, delivery }, "webhook received");

      if (!signature) {
        webhookSignatureFailuresTotal.inc();
        log.warn("missing X-Hub-Signature-256");
        return c.json({ error: "missing signature" }, 401);
      }

      const valid = await webhooks.verify(rawBody, signature).catch(() => false);
      if (!valid) {
        webhookSignatureFailuresTotal.inc();
        log.warn("signature verification failed");
        return c.json({ error: "invalid signature" }, 401);
      }

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(rawBody);
      } catch {
        log.warn("malformed JSON body");
        return c.json({ error: "malformed body" }, 400);
      }

      if (event !== "pull_request" && event !== "pull_request_review" && event !== "issue_comment") {
        log.info({ event }, "unknown event, accepting but not enqueueing");
        return c.json({ received: true, event }, 200);
      }

      const webhookPayload: WebhookPayload = {
        ...payload,
        delivery,
        event,
      };

      const result = await options.queue.enqueue(webhookPayload);

      log.info(
        { event, delivery, idempotencyKey: result.idempotencyKey, inserted: result.inserted },
        "job enqueued",
      );

      return c.json(
        {
          received: true,
          idempotency_key: result.idempotencyKey,
          run_id: result.runId,
        },
        202,
      );
    })(logger, requestRunId);
  };
}
