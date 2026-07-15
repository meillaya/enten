import { z } from "zod";
import { describe, expect, it } from "vitest";

import invalidEnvelope from "../../../../contracts/fixtures/pr-job-envelope.invalid.json" assert { type: "json" };
import validEnvelope from "../../../../contracts/fixtures/pr-job-envelope.valid.json" assert { type: "json" };
import contractSchema from "../../../../contracts/pr-job-envelope.schema.json" assert { type: "json" };
import { PrJobEnvelopeSchema } from "../src/queue/jobs.js";

const jsonSchemaIdentity = z.object({
  $schema: z.literal("https://json-schema.org/draft/2020-12/schema"),
  $id: z.literal("https://enten.dev/contracts/pr-job-envelope.schema.json"),
  additionalProperties: z.literal(false),
});

describe("cross-language pull request job contract", () => {
  it("accepts the shared valid fixture", () => {
    // Given
    const fixture: unknown = validEnvelope;

    // When
    const result = PrJobEnvelopeSchema.safeParse(fixture);

    // Then
    expect(result.success).toBe(true);
  });

  it("rejects the shared invalid fixture", () => {
    // Given
    const fixture: unknown = invalidEnvelope;

    // When
    const result = PrJobEnvelopeSchema.safeParse(fixture);

    // Then
    expect(result.success).toBe(false);
  });

  it("publishes a strict draft-2020-12 schema identity", () => {
    // Given
    const schema: unknown = contractSchema;

    // When
    const parsed = jsonSchemaIdentity.parse(schema);

    // Then
    expect(parsed.additionalProperties).toBe(false);
  });
});
