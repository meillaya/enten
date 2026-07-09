/**
 * Typed application configuration for the enten TS edge.
 *
 * This module is the single source of truth for every environment variable
 * read by the @enten/app package. It is deliberately a *function* —
 * `parse(rawEnv)` — and not a class so that the webhook receiver
 * (T09) and the runner (T10) can share one validated config object
 * without bringing in a DI container.
 *
 * Why a parse function (not a class)?
 *   * The deliverable explicitly requires `{ ok: true; value: Config }`
 *     vs `{ ok: false; error: string }`, which is a discriminated union
 *     the caller narrows with `if (!result.ok)`. A class with
 *     throwing init can't express that.
 *   * The process-env read happens in `server.ts` (so we can defer
 *     side effects until Hono is wired up). The parser stays pure.
 *
 * The matching pattern for redaction is the same one used by the
 * Python `enten.redaction` module so a key that is redacted on one
 * side is redacted on the other; this matters when we ship OTel
 * spans across the language boundary.
 *
 * Pattern source: plan T03 deliverable,
 * `(?i)(api_?key|token|secret|private_?key|password|webhook_?_?secret)`.
 */

import { z } from "zod";

/**
 * Zod schema for the webhook receiver's environment.
 *
 * Field names match the env vars exactly (case-sensitive on the TS
 * side, unlike pydantic's `case_sensitive=False`). Defaults are
 * provided for the listening port, OTel endpoint, and metrics port
 * so a developer with an empty `.env` can still boot the app.
 *
 * `PORT` is intentionally `z.string()` even though Hono would happily
 * take a number — the spec is explicit that it must be parsed as a
 * string and only coerced at the listener. This keeps `parse()` pure
 * and prevents the test suite from being coupled to a node-version-
 * specific number parser.
 */
export const ConfigSchema = z.object({
  GITHUB_APP_ID: z.string(),
  GITHUB_WEBHOOK_SECRET: z.string(),
  DATABASE_URL: z.string(),
  PORT: z.string().default("3000"),
  OTEL_ENDPOINT: z.string().default("http://localhost:4317"),
  METRICS_PORT: z.string().default("9091"),
});

/** Inferred TypeScript type of a fully-parsed `Config`. */
export type Config = z.infer<typeof ConfigSchema>;

/** Successful parse result. */
export type ParseOk = { ok: true; value: Config };

/** Failed parse result. `error` is a human-readable summary. */
export type ParseErr = { ok: false; error: string };

/** The discriminated union `parse()` returns. */
export type ParseResult = ParseOk | ParseErr;

/**
 * Validate a raw env-shaped object against `ConfigSchema`.
 *
 * Pure: does not read `process.env`, does not throw, and does not log.
 * The caller decides what to do with `{ ok: false, error }` (typically
 * a `process.exit(1)` in `server.ts` after printing the error).
 */
export function parse(
  rawEnv: Record<string, string | undefined>,
): ParseResult {
  const result = ConfigSchema.safeParse(rawEnv);
  if (result.success) {
    return { ok: true, value: result.data };
  }
  // Flatten the zod issue tree into a single human-readable line so
  // operators don't have to scroll through a nested error object at
  // boot. We keep the first issue's path so they know *which* var.
  const first = result.error.issues[0];
  const path = first?.path.length ? `${first.path.join(".")}: ` : "";
  return { ok: false, error: `${path}${first?.message ?? "invalid env"}` };
}
