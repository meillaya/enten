import type { Context } from "hono";

export type ReadinessCheck = () => Promise<boolean>;

const SERVICE_NAME = "enten-webhook";

/**
 * Report process liveness. This route deliberately does not touch Postgres so
 * a database outage cannot restart an otherwise healthy edge process.
 */
export function healthHandler(c: Context) {
  return c.json({ status: "ok", service: SERVICE_NAME });
}

/**
 * Report whether the edge can reach its durable queue. A failing check returns
 * 503 so a platform healthcheck withholds traffic instead of routing webhooks
 * to a process that cannot enqueue.
 */
export function readyHandler(check: ReadinessCheck) {
  return async function readinessHandler(c: Context): Promise<Response> {
    let ready: boolean;

    try {
      ready = await check();
    } catch {
      ready = false;
    }

    if (!ready) {
      return c.json({ status: "unavailable", service: SERVICE_NAME }, 503);
    }

    return c.json({ status: "ok", service: SERVICE_NAME });
  };
}
