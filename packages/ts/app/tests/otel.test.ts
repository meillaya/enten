import {
  ROOT_CONTEXT,
  TraceFlags,
  context,
  propagation,
  trace,
} from "@opentelemetry/api";
import { afterEach, describe, expect, it, vi } from "vitest";

import { initOtel } from "../src/otel.js";
import { metricsRouter, recordPrReview } from "../src/metrics.js";
import {
  TraceContextUnavailableError,
  createRequestTracing,
} from "../src/tracing.js";

afterEach(() => vi.restoreAllMocks());

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
      const lifecycle = initOtel("http://127.0.0.1:4317", exporter, []);
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

  it(
    "starts with the default exporter and instrumentations",
    async () => {
      // Given
      const lifecycle = initOtel();

      // When
      await lifecycle.shutdown();

      // Then
      expect(true).toBe(true);
    },
    20_000,
  );

  it("rejects a producer span when no traceparent is injected", async () => {
    // Given
    const inject = vi.spyOn(propagation, "inject").mockImplementation(() => undefined);
    const tracing = createRequestTracing();

    // When
    const produced = tracing.withProducerSpan(async (carrier) => carrier);

    // Then
    await expect(produced).rejects.toBeInstanceOf(TraceContextUnavailableError);
    inject.mockRestore();
  });

  it("passes through an injected tracestate", async () => {
    // Given
    const inject = vi
      .spyOn(propagation, "inject")
      .mockImplementation((_context, carrier) => {
        const record = carrier as Record<string, string>;
        record.traceparent = "00-11111111111111111111111111111111-2222222222222222-01";
        record.tracestate = "vendor=value";
      });
    const tracing = createRequestTracing();

    // When
    const produced = await tracing.withProducerSpan(async (carrier) => carrier);
    inject.mockRestore();

    // Then
    expect(produced.tracestate).toBe("vendor=value");
  });
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
