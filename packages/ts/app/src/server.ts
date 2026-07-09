/**
 * enten HTTP entry point.
 *
 * Exposes:
 *   GET  /health   -> 200 { status: "ok", service: "enten-webhook" }
 *   GET  /metrics  -> text/plain Prometheus metrics
 *   POST /webhook  -> GitHub App webhook receiver with signature validation
 *
 * The Hono `app` is exported for tests; `start()` actually binds to
 * PORT / METRICS_PORT via `@hono/node-server` when the module is run
 * directly (e.g. `node dist/server.js`).
 */

import { serve } from "@hono/node-server";
import { Hono } from "hono";
import postgres from "postgres";

import { parse, type Config } from "./config.js";
import { createLogger } from "./logging.js";
import { createJobsQueue, type JobsQueue } from "./queue/jobs.js";
import { healthHandler } from "./routes/health.js";
import { metricsHandler } from "./routes/metrics.js";
import { createWebhookHandler } from "./routes/webhook.js";

const logger = createLogger("server");

export interface CreateAppOptions {
  secret: string;
  databaseUrl: string;
  sql?: postgres.Sql;
  queue?: JobsQueue;
}

export function createApp(options: CreateAppOptions) {
  const app = new Hono();

  const pgUrl = options.databaseUrl.replace(/^postgresql\+asyncpg:\/\//, "postgresql://");
  const sql = options.sql ?? postgres(pgUrl, {
    max: 10,
    idle_timeout: 20,
    connect_timeout: 10,
  });

  const queue = options.queue ?? createJobsQueue({ sql });

  app.get("/health", healthHandler);
  app.get("/metrics", metricsHandler);
  app.post("/webhook", createWebhookHandler({ secret: options.secret, queue }));

  return { app, sql, queue };
}

export function loadConfig(env: Record<string, string | undefined>): Config {
  const envResult = parse(env);
  if (!envResult.ok) {
    logger.error({ error: envResult.error }, "configuration invalid");
    process.exit(1);
  }
  return envResult.value;
}

const config = loadConfig(process.env);
const { app, sql } = createApp({
  secret: config.GITHUB_WEBHOOK_SECRET,
  databaseUrl: config.DATABASE_URL,
});

export function start(options?: { serve?: typeof serve }): void {
  const doServe = options?.serve ?? serve;
  const port = parseInt(config.PORT, 10);
  const metricsPort = parseInt(config.METRICS_PORT, 10);

  doServe({
    fetch: app.fetch,
    port,
  } as Parameters<typeof serve>[0]);
  logger.info({ port }, "webhook server listening");

  if (metricsPort !== port) {
    doServe({
      fetch: app.fetch,
      port: metricsPort,
    } as Parameters<typeof serve>[0]);
    logger.info({ port: metricsPort }, "metrics server listening");
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  start();
}

export default app;
export { app, config, sql };
