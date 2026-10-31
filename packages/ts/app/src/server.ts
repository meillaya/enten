import { serve, type ServerType } from "@hono/node-server";
import { Hono } from "hono";
import postgres from "postgres";

import { parse, type Config } from "./config.js";
import { createLogger } from "./logging.js";
import {
  computeReviewContract,
  createJobsQueue,
  type JobEnqueuer,
} from "./queue/jobs.js";
import {
  healthHandler,
  readyHandler,
  type ReadinessCheck,
} from "./routes/health.js";
import { metricsHandler } from "./routes/metrics.js";
import { createWebhookHandler } from "./routes/webhook.js";
import { initOtel, type OtelLifecycle } from "./otel.js";
import { createRequestTracing, type RequestTracing } from "./tracing.js";

const logger = createLogger("server");

export interface CreateAppOptions {
  readonly secret: string;
  readonly botSlug?: string;
  readonly databaseUrl: string;
  readonly sql?: postgres.Sql;
  readonly queue?: JobEnqueuer;
  readonly tracing?: RequestTracing;
  readonly maxWebhookBytes?: number;
  readonly reviewContract?: string;
  readonly readyCheck?: ReadinessCheck;
  readonly verifySignature?: (
    body: string,
    signature: string,
  ) => Promise<boolean>;
}

export type CreatedApp = {
  readonly app: Hono;
  readonly metricsApp: Hono;
  readonly sql: postgres.Sql;
  readonly queue: JobEnqueuer;
};

export class ConfigurationError extends Error {
  override readonly name = "ConfigurationError";

  constructor(readonly detail: string) {
    super(`configuration invalid: ${detail}`);
  }
}

export function createApp(options: CreateAppOptions): CreatedApp {
  const app = new Hono();
  const pgUrl = options.databaseUrl.replace(
    /^postgresql\+asyncpg:\/\//,
    "postgresql://",
  );
  const sql =
    options.sql ??
    postgres(pgUrl, {
      max: 10,
      idle_timeout: 20,
      connect_timeout: 2,
      connection: {
        statement_timeout: 3_000,
        lock_timeout: 1_000,
      },
    });
  const queue = options.queue ?? createJobsQueue({ sql });
  const tracing = options.tracing ?? createRequestTracing();
  const readyCheck =
    options.readyCheck ??
    (async (): Promise<boolean> => {
      await sql`SELECT 1`;
      return true;
    });

  app.get("/health", healthHandler);
  app.get("/ready", readyHandler(readyCheck));
  app.post(
    "/webhook",
    createWebhookHandler({
      secret: options.secret,
      botSlug: options.botSlug ?? "enten-bot",
      queue,
      tracing,
      reviewContract: options.reviewContract ?? "0".repeat(64),
      maxWebhookBytes: options.maxWebhookBytes ?? 1024 * 1024,
      ...(options.verifySignature === undefined
        ? {}
        : { verifySignature: options.verifySignature }),
    }),
  );

  // The metrics listener is a separate app so the public webhook port never
  // serves the Prometheus scrape surface.
  const metricsApp = new Hono();
  metricsApp.get("/health", healthHandler);
  metricsApp.get("/metrics", metricsHandler);

  return { app, metricsApp, sql, queue };
}

export function loadConfig(
  env: Record<string, string | undefined>,
): Config {
  const result = parse(env);
  if (!result.ok) {
    throw new ConfigurationError(result.error);
  }
  return result.value;
}

export type Runtime = {
  readonly app: Hono;
  readonly sql: postgres.Sql;
  shutdown(): Promise<void>;
};

export type StartOptions = {
  readonly config?: Config;
  readonly env?: Record<string, string | undefined>;
  readonly serve?: typeof serve;
  readonly initTelemetry?: (endpoint: string) => OtelLifecycle;
};

export type LoggedError = { readonly error: Error | string };

export function toLoggedError(error: unknown): LoggedError {
  if (error instanceof Error) {
    return { error };
  }
  return { error: String(error) };
}

function closeServer(server: ServerType): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error === undefined) {
        resolve();
        return;
      }
      reject(error);    });
  });
}

export async function start(options: StartOptions = {}): Promise<Runtime> {
  const config = options.config ?? loadConfig(options.env ?? process.env);
  const telemetry = (options.initTelemetry ?? initOtel)(config.OTEL_ENDPOINT);
  const created = createApp({
    secret: config.GITHUB_WEBHOOK_SECRET,
    botSlug: config.GITHUB_APP_SLUG,
    databaseUrl: config.DATABASE_URL,
    reviewContract: computeReviewContract(
      config.VERIFICATION_COMMAND,
      config.SANDBOX_DOCKER_IMAGE,
    ),
  });
  const listen = options.serve ?? serve;
  const webhookPort = Number(config.PORT);
  const metricsPort = Number(config.METRICS_PORT);

  // An operator who collapses both ports onto one listener explicitly opts
  // into exposing the scrape route beside the webhook route.
  if (metricsPort === webhookPort) {
    created.app.route("/", created.metricsApp);
  }

  const servers = [listen({ fetch: created.app.fetch, port: webhookPort })];
  if (metricsPort !== webhookPort) {
    servers.push(
      listen({ fetch: created.metricsApp.fetch, port: metricsPort }),
    );
  }

  let shutdownPromise: Promise<void> | undefined;
  const onSignal = (): void => {
    shutdown().catch((error: unknown) => {
      logger.error(toLoggedError(error), "graceful shutdown failed");
    });
  };

  async function drain(): Promise<void> {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    await Promise.all(servers.map(closeServer));
    await created.sql.end({ timeout: 5 });
    await telemetry.shutdown();
  }

  function shutdown(): Promise<void> {
    shutdownPromise ??= drain();
    return shutdownPromise;
  }

  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  logger.info({ port: webhookPort }, "webhook server listening");
  if (metricsPort !== webhookPort) {
    logger.info({ port: metricsPort }, "metrics server listening");
  }

  return { app: created.app, sql: created.sql, shutdown };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    await start();
  } catch (error: unknown) {
    logger.error(toLoggedError(error), "server startup failed");
    process.exitCode = 1;
  }
}
