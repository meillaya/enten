/**
 * Structured logging configuration for the enten TypeScript edge.
 *
 * The edge emits one JSON line per event via `pino` and redacts the
 * explicitly configured secret paths before writing to stdout.
 *
 * Design contract
 * ---------------
 *
 * - One JSON object per `stdout.write`, flushable line-by-line by `tail`.
 * - `withRunId` explicitly passes a child logger bound to one `run_id`.
 * - Secret paths in the pino `redact` array are censored at emission.
 *
 * Run ids
 * -------
 *
 * `newRunId()` produces values matching `^pr_[a-z0-9]{12}$`.
 * The twelve-character suffix encodes hash output derived from a monotonic
 * clock reading and 16 random bits. IDs are probabilistically collision
 * resistant rather than guaranteed unique.
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
 *   "level": "info",
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
 * redacted at emission time via pino's explicit `redact` paths.
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
        "api_key",
        "token",
        "webhook_secret",
        "password",
        "private_key",
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
 * Return a 15-character run id matching `^pr_[a-z0-9]{12}$`.
 *
 * Implementation mirrors the Python `enten.logging.new_run_id`:
 *
 *   mix  = sha256( `${performance.now_ns}-${randomBytes(2)}` )
 *   id   = "pr_" + 12 chars from base32(lowercase(hex(mix)))
 *
 * Performance.now() is monotonic and high-resolution; `randomBytes(2)`
 * adds 16 bits of CSPRNG entropy before hashing.
 *
 * The bit-extraction loop walks 15 hex digits (60 bits / 4 bits per
 * hex digit) and packs five bits at a time into one of twelve base32
 * characters. Adjacent nibbles are shifted and combined when a group
 * straddles two hex digits.
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
  const suffix: string[] = new Array(12).fill(BASE32_ALPHABET.charAt(0));
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
    suffix[i] = BASE32_ALPHABET.charAt(loBits | hiBits);
  }
  return `${RUN_ID_PREFIX}${suffix.join("")}`;
}
