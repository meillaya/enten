import { z } from "zod";

export const ConfigSchema = z.object({
  GITHUB_APP_ID: z.string(),
  GITHUB_APP_SLUG: z.string().min(1).default("enten-bot"),
  GITHUB_WEBHOOK_SECRET: z.string(),
  DATABASE_URL: z.string(),
  PORT: z.string().default("3000"),
  OTEL_ENDPOINT: z.string().default("http://localhost:4317"),
  METRICS_PORT: z.string().default("9091"),
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
