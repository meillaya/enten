import { z } from "zod";

export const ConfigSchema = z.object({
  GITHUB_APP_ID: z.string(),
  GITHUB_APP_SLUG: z.string().min(1).default("enten-bot"),
  GITHUB_WEBHOOK_SECRET: z.string().min(32),
  DATABASE_URL: z.string(),
  PORT: z.string().default("3000"),
  OTEL_ENDPOINT: z.string().default("http://localhost:4317"),
  METRICS_PORT: z.string().default("9091"),
  SANDBOX_DOCKER_IMAGE: z
    .string()
    .min(1)
    .default(
      "python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28",
    ),
  VERIFICATION_COMMAND: z.string().min(1).default("python -m compileall -q ."),
});

export type Config = z.infer<typeof ConfigSchema>;

export type ParseOk = { ok: true; value: Config };

export type ParseErr = { ok: false; error: string };

export type ParseResult = ParseOk | ParseErr;

export function parse(
  rawEnv: Record<string, string | undefined>,
): ParseResult {
  const result = ConfigSchema.safeParse(rawEnv);
  if (result.success) {
    return { ok: true, value: result.data };
  }
  const first = result.error.issues[0]!;
  const path = first.path.length > 0 ? `${first.path.join(".")}: ` : "";
  return { ok: false, error: `${path}${first.message}` };
}
