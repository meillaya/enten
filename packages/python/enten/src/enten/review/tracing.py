from __future__ import annotations

import re

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


class JobTraceError(ValueError):
    """Raised when a queued trace carrier cannot be trusted."""


def extract_job_context(
    *,
    traceparent: str,
    tracestate: str | None,
    expected_trace_id: str,
) -> Context:
    """Extract and cross-check the authenticated producer context."""
    if not _TRACE_ID.fullmatch(expected_trace_id) or expected_trace_id == "0" * 32:
        raise JobTraceError("invalid expected trace identifier")

    carrier = {"traceparent": traceparent}
    if tracestate is not None:
        carrier["tracestate"] = tracestate
    context = TraceContextTextMapPropagator().extract(carrier=carrier)
    span_context = trace.get_current_span(context).get_span_context()
    if not span_context.is_valid or not span_context.is_remote:
        raise JobTraceError("invalid queued trace carrier")

    actual_trace_id = f"{span_context.trace_id:032x}"
    if actual_trace_id != expected_trace_id:
        raise JobTraceError(
            f"queued trace {actual_trace_id} does not match database trace {expected_trace_id}"
        )
    return context


__all__ = ["JobTraceError", "extract_job_context"]
