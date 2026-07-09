/**
 * Prometheus metrics exposition for the @enten/app TypeScript edge.
 *
 * Mirrors the Python surface in `packages/python/enten/src/enten/metrics.py`
 * so that both halves of the monorepo expose the same counters and labels.
 * The router is mounted on `/metrics` and returns the default registry in
 * Prometheus text format.
 */

import { Hono } from "hono";
import { Counter, register } from "prom-client";

/**
 * Total number of PR reviews processed by the agent, labelled by result.
 */
const prReviewsTotal = new Counter({
  name: "pr_reviews_total",
  help: "Total number of PR reviews processed",
  labelNames: ["result"],
});

/**
 * Total GitHub webhook requests received by the agent.
 */
export const webhookRequestsTotal = new Counter({
  name: "enten_webhook_requests_total",
  help: "Total GitHub webhook requests received",
});

/**
 * Total webhook requests that failed signature validation.
 */
export const webhookSignatureFailuresTotal = new Counter({
  name: "enten_webhook_signature_failures_total",
  help: "Total webhook requests that failed signature validation",
});

/**
 * Increment the `pr_reviews_total` counter for the given result.
 *
 * `result` should be one of `passed`, `failed`, or `errored` so that the
 * TS and Python metrics stay aligned.
 */
export function recordPrReview(result: string): void {
  prReviewsTotal.inc({ result });
}

/**
 * Return a Hono router that serves Prometheus metrics on `/metrics`.
 *
 * The endpoint renders the default `prom-client` registry in text format.
 * It is async because `register.metrics()` returns a Promise in
 * `prom-client` v15+.
 */
export function metricsRouter(): Hono {
  const app = new Hono();

  app.get("/metrics", async (c) => {
    const metrics = await register.metrics();
    return c.text(metrics, 200, {
      "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
    });
  });

  return app;
}
