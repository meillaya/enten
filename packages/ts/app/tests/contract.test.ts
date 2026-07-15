import { z } from "zod";
import { describe, expect, it } from "vitest";

import invalidEnvelope from "../../../../contracts/fixtures/pr-job-envelope.invalid.json" assert { type: "json" };
import invalidCoercions from "../../../../contracts/fixtures/pr-job-envelope.invalid-coercions.json" assert { type: "json" };
import validEnvelope from "../../../../contracts/fixtures/pr-job-envelope.valid.json" assert { type: "json" };
import validDeliveryIds from "../../../../contracts/fixtures/pr-job-envelope.valid-delivery-ids.json" assert { type: "json" };
import contractSchema from "../../../../contracts/pr-job-envelope.schema.json" assert { type: "json" };
import { PrJobEnvelopeSchema } from "../src/queue/jobs.js";

const jsonSchemaIdentity = z.object({
  $schema: z.literal("https://json-schema.org/draft/2020-12/schema"),
  $id: z.literal("https://enten.dev/contracts/pr-job-envelope.schema.json"),
  additionalProperties: z.literal(false),
});

const coercionCases = z.array(
  z.object({
    path: z.array(z.string()).min(1),
    value: z.string(),
  }),
);

function replaceAtPath(
  source: Record<string, unknown>,
  path: readonly string[],
  value: unknown,
): Record<string, unknown> {
  const candidate = structuredClone(source);
  let target = candidate;
  for (const segment of path.slice(0, -1)) {
    const nested = target[segment];
    if (typeof nested !== "object" || nested === null || Array.isArray(nested)) {
      throw new Error(`invalid fixture path: ${path.join(".")}`);
    }
    target = nested as Record<string, unknown>;
  }
  const finalSegment = path.at(-1);
  if (finalSegment === undefined) {
    throw new Error("invalid empty fixture path");
  }
  target[finalSegment] = value;
  return candidate;
}

describe("cross-language pull request job contract", () => {
  it("accepts the shared valid fixture", () => {
    // Given
    const fixture: unknown = validEnvelope;

    // When
    const result = PrJobEnvelopeSchema.safeParse(fixture);

    // Then
    expect(result.success).toBe(true);
  });

  it("accepts every shared canonical delivery identifier", () => {
    const deliveryIds = z.array(z.string()).parse(validDeliveryIds);
    for (const deliveryId of deliveryIds) {
      expect(
        PrJobEnvelopeSchema.safeParse({ ...validEnvelope, delivery_id: deliveryId }).success,
      ).toBe(true);
    }
  });

  it("rejects the shared invalid fixture", () => {
    // Given
    const fixture: unknown = invalidEnvelope;

    // When
    const result = PrJobEnvelopeSchema.safeParse(fixture);

    // Then
    expect(result.success).toBe(false);
  });

  it("rejects every shared coercion fixture", () => {
    const cases = coercionCases.parse(invalidCoercions);
    for (const testCase of cases) {
      const candidate = replaceAtPath(validEnvelope, testCase.path, testCase.value);
      expect(PrJobEnvelopeSchema.safeParse(candidate).success).toBe(false);
    }
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
