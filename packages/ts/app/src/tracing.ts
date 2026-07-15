import {
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  context,
  propagation,
  trace,
  type Span,
} from "@opentelemetry/api";

export type ProducedTrace = {
  readonly traceId: string;
  readonly traceparent: string;
  readonly tracestate?: string;
};

export interface RequestTracing {
  withServerSpan<T>(operation: () => Promise<T>): Promise<T>;
  withProducerSpan<T>(
    operation: (produced: ProducedTrace) => Promise<T>,
  ): Promise<T>;
}

export class TraceContextUnavailableError extends Error {
  override readonly name = "TraceContextUnavailableError";

  constructor() {
    super("OpenTelemetry did not inject a W3C traceparent");
  }
}

function recordFailure(span: Span, error: unknown): void {
  span.setStatus({ code: SpanStatusCode.ERROR });
  if (error instanceof Error) {
    span.recordException(error);
    return;
  }
  span.recordException(String(error));
}

export function createRequestTracing(): RequestTracing {
  const tracer = trace.getTracer("@enten/app");

  return {
    withServerSpan<T>(operation: () => Promise<T>): Promise<T> {
      return tracer.startActiveSpan(
        "POST /webhook",
        { kind: SpanKind.SERVER },
        ROOT_CONTEXT,
        async (span) => {
          try {
            return await operation();
          } catch (error: unknown) {
            recordFailure(span, error);
            throw error;
          } finally {
            span.end();
          }
        },
      );
    },

    withProducerSpan<T>(
      operation: (produced: ProducedTrace) => Promise<T>,
    ): Promise<T> {
      return tracer.startActiveSpan(
        "jobs enqueue",
        { kind: SpanKind.PRODUCER },
        async (span) => {
          try {
            const carrier: Record<string, string> = {};
            propagation.inject(context.active(), carrier);
            const traceparent = carrier["traceparent"];
            if (traceparent === undefined) {
              throw new TraceContextUnavailableError();
            }

            const spanContext = span.spanContext();
            const tracestate = carrier["tracestate"];
            const produced =
              tracestate === undefined
                ? { traceId: spanContext.traceId, traceparent }
                : { traceId: spanContext.traceId, traceparent, tracestate };
            return await operation(produced);
          } catch (error: unknown) {
            recordFailure(span, error);
            throw error;
          } finally {
            span.end();
          }
        },
      );
    },
  };
}
