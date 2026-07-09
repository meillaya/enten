/**
 * enten HTTP entry point (scaffold).
 *
 * The full webhook / event-routing surface is built in T05 (config) and
 * T08 (db/migrations). This stub exists so the TypeScript workspace is
 * end-to-end buildable from day one and ``GET /health`` returns 200.
 *
 * The Hono ``app`` is exported so it can be mounted by a future
 * ``server.ts`` (T04: runtime bootstrap) or invoked directly from tests
 * via ``app.fetch(new Request("http://x/health"))``.
 */

import { Hono } from "hono";

const app = new Hono();

app.get("/health", (c) =>
  c.json({
    status: "ok",
    service: "enten",
    version: "0.1.0",
  }),
);

export default app;
export { app };
