/**
 * OpenTelemetry bootstrap for the @enten/app TypeScript edge.
 *
 * This module wires the Node SDK to a Jaeger OTLP gRPC exporter and enables
 * the Node auto-instrumentation bundle (HTTP, Express, and any other
 * supported frameworks it detects). Hono is not instrumented directly by
 * the OpenTelemetry project at the time of writing, so HTTP-level
 * instrumentation covers the inbound request path.
 *
 * Call `initOtel()` once during process startup, before the Hono app starts
 * listening. The exporter URL is read from `process.env.OTEL_ENDPOINT` and
 * falls back to the local Jaeger collector at `http://localhost:4317`.
 */

import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-grpc";
import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";

/**
 * Configure and start the OpenTelemetry Node SDK.
 *
 * This function is intentionally side-effecting: it registers the global
 * tracer provider and patches Node's built-in modules for automatic
 * propagation of trace context. It does not return the SDK instance; tests
 * only need to assert that it does not throw.
 */
export function initOtel(): void {
  const sdk = new NodeSDK({
    traceExporter: new OTLPTraceExporter({
      url: process.env.OTEL_ENDPOINT || "http://localhost:4317",
    }),
    instrumentations: [getNodeAutoInstrumentations()],
  });

  sdk.start();
}
