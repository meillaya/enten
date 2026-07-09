/**
 * Structured logging configuration for the enten TypeScript edge.
 *
 * The agent emits one JSON line per event via `pino`. The redaction list
 * is the single source of truth for what an enten log line looks like on
 * disk; downstream tooling (Loki, Jaeger log appenders, the typecheck
 * CI step) all match the same redaction pattern as the Python side.
 *
 * Design contract
 * ---------------
 *
 * - One JSON object per `stdout.write`, flushable line-by-line by `tail`.
 * - Bound `run_id` contextvars flow into every subsequent log call of the
 *   same handler invocation; `withRunId` is the convenience wrapper that
 *   binds it for the lifetime of an HTTP request.
 * - Secret-shaped keys are redacted via pino's built-in `redact` array;
 *   the pattern mirrors `packages/python/enten/src/enten/redaction.py`
 *   so a key redacted on one side is redacted on the other.
 *
 * Run ids
 * -------
 *
 * `newRunId()` produces values matching `^pr_[a-z0-9]{12}$`.
 * Twelve lowercase base32 chars carry 60 bits of entropy; mixed with
 * `performance.now()` + `crypto.randomBytes(2)` so collisions over a
 * single process's lifetime are vanishingly unlikely.
 */

import { createHash, randomBytes } from "node:crypto";
import { pino, type Logger } from "pino";

/** Prefix on every enten run id; the suffix is base32-lowercase. */
const RUN_ID_PREFIX = "pr_";

/** Crockford-style base32 alphabet used to encode run-id suffixes. */
const BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

/**
 * Configure a pino logger for an enten module.
 *
 * The returned logger emits JSON to stdout with a stable shape:
 *
 * ```json
 * {
 *   "level": 30,
 *   "time": 1735738800000,
 *   "service": "enten-webhook",
 *   "module": "<name>",
 *   "msg": "...",
 *   ...
 * }
 * ```
 *
 * The `level` field is reformatted from pino's numeric default into the
 * string label (`"info"`, `"warn"`, etc.) so log searches across the
 * Python and TypeScript sides share a single keyword. Secrets are
 * redacted at emission time via pino's `redact` array using the same
 * key set as the Python `redaction.py` module.
 *
 * @param name - The module name recorded on every line as `"module"`.
 *   Conventionally a noun describing what the logger is for, e.g.
 *   `"webhook.receiver"` or `"queue.dispatcher"`.
 */
export function createLogger(name: string): Logger {
  return pino({
    base: { service: "enten-webhook", module: name },
    formatters: {
      level(label: string): Record<string, string> {
        return { level: label };
      },
    },
    redact: {
      paths: [
        "*.api_key",
        "*.token",
        "*.webhook_secret",
        "*.password",
        "*.private_key",
      ],
      censor: "[REDACTED]",
    },
  });
}

/**
 * Alias for {@link createLogger} matching the spec's "Export as
 * `pinoLogger` factory (test code uses it)" phrasing. Tests can import
 * either name interchangeably.
 */
export const pinoLogger = createLogger;

/**
 * Wrap a handler so its logger is bound to a `run_id` for the lifetime
 * of one call. Used by HTTP handlers (webhook receiver T09, runner T10)
 * where one inbound request should log as a single correlation unit.
 *
 * @example
 * ```ts
 * export const handler = withRunId(async (logger, runId) => {
 *   logger.info({ runId, pr: 42 }, "dispatched"); // runId is in the line
 * });
 * ```
 *
 * The returned closure accepts `(parentLogger, runId)`; the parent
 * logger is typically the module-scoped one created via `createLogger`
 * so all module-level fields (`service`, `module`) survive into the
 * inner child logger.
 */
export function withRunId<T>(
  handler: (logger: Logger, runId: string) => T | Promise<T>,
): (logger: Logger, runId: string) => Promise<T> {
  return async (logger: Logger, runId: string): Promise<T> => {
    const child = logger.child({ run_id: runId });
    return handler(child, runId);
  };
}

/**
 * Return a 16-character run id matching `^pr_[a-z0-9]{12}$`.
 *
 * Implementation mirrors the Python `enten.logging.new_run_id`:
 *
 *   mix  = sha256( `${performance.now_ns}-${randomBytes(2)}` )
 *   id   = "pr_" + 12 chars from base32(lowercase(hex(mix)))
 *
 * Performance.now() is monotonic and high-resolution; `randomBytes(2)`
 * provides 16 bits of CSPRNG entropy that guarantee uniqueness over
 * 1000+ consecutive calls even if the monotonic clock is identical.
 *
 * The bit-extraction loop walks 15 hex digits (60 bits / 4 bits per
 * hex digit) and packs five bits at a time into one of twelve base32
 * characters. The neighbour-byte xor pattern handles bit offsets that
 * straddle two hex digits without a Buffer intermediate.
 */
export function newRunId(): string {
  // Performance.now() returns a sub-millisecond float; multiplying by
  // 1_000_000 yields nanoseconds, sufficient resolution for our mix.
  const monotonicNs = Math.floor(performance.now() * 1_000_000).toString();
  const entropy = randomBytes(2).toString("hex"); // 4 hex chars, 16 bits
  const digest = createHash("sha256")
    .update(`${monotonicNs}-${entropy}`)
    .digest("hex");

  // Walk the first 60 bits of the digest (15 hex digits) and pack each
  // group of 5 bits into one base32 char. Twelve groups = 12 chars.
  const suffix: string[] = new Array(12).fill(BASE32_ALPHABET[0]!);
  for (let i = 0; i < 12; i += 1) {
    const bitOffset = i * 5;
    const hexStart = Math.floor(bitOffset / 4);
    const nibbleOffset = bitOffset % 4;
    const lo = parseInt(digest[hexStart] ?? "0", 16);
    const hi = parseInt(digest[hexStart + 1] ?? "0", 16);
    // 5 bits straddling two hex digits: shift `lo` up by `nibbleOffset`
    // and pull the remaining bits from `hi`.
    const loBits = (lo << nibbleOffset) & 0x1f;
    const hiBits = nibbleOffset === 0 ? 0 : hi >> (4 - nibbleOffset);
    suffix[i] = BASE32_ALPHABET[loBits | hiBits]!;
  }
  return `${RUN_ID_PREFIX}${suffix.join("")}`;
}
