import type { Context } from "hono";
import { register } from "prom-client";

export async function metricsHandler(c: Context) {
  const metrics = await register.metrics();
  return c.text(metrics, 200, {
    "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
  });
}
