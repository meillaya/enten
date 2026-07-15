from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from enten.review.tracing import JobTraceError, extract_job_context

TRACE_ID = "11111111111111111111111111111111"
PARENT_ID = "2222222222222222"
TRACEPARENT = f"00-{TRACE_ID}-{PARENT_ID}-01"


def test_consumer_and_pipeline_spans_continue_producer_trace() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    parent = extract_job_context(
        traceparent=TRACEPARENT,
        tracestate="vendor=value",
        expected_trace_id=TRACE_ID,
    )

    with (
        tracer.start_as_current_span(
            "pr_review.consume",
            context=parent,
            kind=SpanKind.CONSUMER,
        ) as consumer,
        tracer.start_as_current_span("pipeline.snapshot"),
    ):
        pass

    spans = {span.name: span for span in exporter.get_finished_spans()}
    consumer_span = spans["pr_review.consume"]
    child_span = spans["pipeline.snapshot"]
    assert consumer_span.context is not None
    assert f"{consumer_span.context.trace_id:032x}" == TRACE_ID
    assert consumer_span.parent is not None
    assert f"{consumer_span.parent.span_id:016x}" == PARENT_ID
    assert consumer_span.parent.is_remote
    assert child_span.parent is not None
    assert child_span.parent.span_id == consumer.get_span_context().span_id


def test_job_trace_rejects_database_trace_mismatch() -> None:
    with pytest.raises(JobTraceError, match="does not match"):
        extract_job_context(
            traceparent=TRACEPARENT,
            tracestate=None,
            expected_trace_id="3" * 32,
        )


@pytest.mark.parametrize(
    "traceparent",
    [
        "invalid",
        "00-00000000000000000000000000000000-2222222222222222-01",
        "00-11111111111111111111111111111111-0000000000000000-01",
    ],
)
def test_job_trace_rejects_invalid_carrier(traceparent: str) -> None:
    with pytest.raises(JobTraceError, match="invalid"):
        extract_job_context(
            traceparent=traceparent,
            tracestate=None,
            expected_trace_id=TRACE_ID,
        )
