import { ROOT_CONTEXT, TraceFlags, context, trace } from "@opentelemetry/api";
import { describe, expect, it } from "vitest";

import { initOtel } from "../src/otel.js";
import { metricsRouter, recordPrReview } from "../src/metrics.js";
import { createRequestTracing } from "../src/tracing.js";

describe("otel", () => {
  it(
    "creates a new server root, child producer, W3C carrier, and drainable lifecycle",
    async () => {
      // Given
      const inboundTraceId = "99999999999999999999999999999999";
      const exporter = {
        export: (_spans: readonly unknown[], callback: (result: { code: number }) => void) =>
          callback({ code: 0 }),
        forceFlush: async () => undefined,
        shutdown: async () => undefined,
      };
      const lifecycle = initOtel(undefined, exporter, []);
      const tracing = createRequestTracing();
      const inboundContext = trace.setSpanContext(ROOT_CONTEXT, {
        traceId: inboundTraceId,
        spanId: "8888888888888888",
        traceFlags: TraceFlags.SAMPLED,
        isRemote: true,
      });

      // When
      const produced = await context.with(inboundContext, () =>
        tracing.withServerSpan(() =>
          tracing.withProducerSpan(async (carrier) => carrier),
        ),
      );
      const serverFailure = new Error("server operation failed");
      await expect(
        tracing.withServerSpan(() => Promise.reject(serverFailure)),
      ).rejects.toBe(serverFailure);
      const producerFailure = new Error("producer operation failed");
      await expect(
        tracing.withProducerSpan(() => Promise.reject(producerFailure)),
      ).rejects.toBe(producerFailure);
      const nonErrorFailure = { kind: "test_failure" };
      await expect(
        tracing.withServerSpan(() => Promise.reject(nonErrorFailure)),
      ).rejects.toBe(nonErrorFailure);
      await expect(lifecycle.shutdown()).resolves.toBeUndefined();

      // Then
      expect(produced.traceId).not.toBe(inboundTraceId);
      expect(produced.traceparent.slice(3, 35)).toBe(produced.traceId);
    },
    15_000,
  );
});

describe("metrics", () => {
  it("metricsRouter returns 200 with pr_reviews_total after recordPrReview", async () => {
    recordPrReview("passed");

    const app = metricsRouter();
    const res = await app.request("/metrics");

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("pr_reviews_total");
    expect(body).toContain('result="passed"');
  });
});
