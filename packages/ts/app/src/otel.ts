import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-grpc";
import {
  NodeSDK,
  type NodeSDKConfiguration,
} from "@opentelemetry/sdk-node";

export interface OtelLifecycle {
  shutdown(): Promise<void>;
}

export function initOtel(
  endpoint = process.env.OTEL_ENDPOINT,
  exporter?: NodeSDKConfiguration["traceExporter"],
  instrumentations?: NodeSDKConfiguration["instrumentations"],
): OtelLifecycle {
  const sdk = new NodeSDK({
    traceExporter:
      exporter ??
      new OTLPTraceExporter({
        url: endpoint ?? "http://localhost:4317",
        timeoutMillis: 1_000,
      }),
    instrumentations: instrumentations ?? [getNodeAutoInstrumentations()],
  });

  sdk.start();
  return {
    shutdown: () => sdk.shutdown(),
  };
}
