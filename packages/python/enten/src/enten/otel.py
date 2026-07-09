"""OpenTelemetry bootstrap for the enten Python core.

This module is the single entry point for tracing. It wires the global
:class:`~opentelemetry.sdk.trace.TracerProvider` to a Jaeger-backed OTLP
gRPC exporter, attaches the package version to every resource, and enables
the auto-instrumentation hooks we rely on (``httpx``, ``asyncpg``,
``asyncio``). Callers should invoke :func:`configure_otel` once at process
startup; after that :func:`tracer` returns the global tracer and
:func:`add_span_attribute` is a convenience wrapper for the current span.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.resource import ResourceAttributes

from enten import __version__
from enten.config import Settings


def configure_otel(
    service_name: str = "enten-cli",
    otel_endpoint: str | None = None,
) -> None:
    """Configure the global OTel SDK for this process.

    The exporter endpoint is resolved in the following order:

    1. The ``otel_endpoint`` argument if it is non-``None``.
    2. The ``OTEL_ENDPOINT`` environment variable (read via ``Settings``).
    3. ``http://localhost:4317`` (the Jaeger OTLP gRPC default used by the
       local docker-compose stack).

    Auto-instrumentation is applied idempotently; calling this function more
    than once in the same process is safe but will log a warning from the
    OTel SDK because the global tracer provider can only be set once.
    """
    endpoint = otel_endpoint or Settings().otel_endpoint or "http://localhost:4317"

    resource = Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: service_name,
            ResourceAttributes.SERVICE_VERSION: __version__,
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument the libraries the agent uses. Each instrumentor is
    # idempotent and will no-op on subsequent calls.
    HTTPXClientInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()
    AsyncioInstrumentor().instrument()


def tracer() -> trace.Tracer:
    """Return the global tracer for the ``enten`` package."""
    return trace.get_tracer(__name__)


def add_span_attribute(key: str, value: Any) -> None:
    """Add an attribute to the current active span, if one exists.

    This is a convenience wrapper so business code does not have to import
    ``opentelemetry.trace`` directly. If there is no active span this is a
    silent no-op.
    """
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(key, value)
