"""Tests for ``enten.otel`` and ``enten.metrics``.

OTel tests use an :class:`~opentelemetry.sdk.trace.export.InMemorySpanExporter`
substituted for the production OTLP exporter so the suite has no runtime
dependency on a Jaeger collector. The metrics test binds to a temporary port
and scrapes the exposition endpoint with ``httpx``.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from enten import __version__
from enten.metrics import (
    record_llm_call,
    record_pr_review,
    record_sandbox_exec,
    start_metrics_server,
)
from enten.otel import add_span_attribute, configure_otel, tracer


def _free_port() -> int:
    """Return an ephemeral TCP port that is free on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _InMemoryExporter(InMemorySpanExporter):
    """In-memory exporter that tolerates the OTLP constructor signature."""

    def __init__(self, **_kwargs: Any) -> None:
        super().__init__()


@pytest.fixture(scope="module")
def memory_exporter() -> InMemorySpanExporter:
    """Configure OTel once for the module with an in-memory exporter."""
    mp = pytest.MonkeyPatch()
    mp.setattr(
        "enten.otel.OTLPSpanExporter",
        _InMemoryExporter,
    )
    configure_otel("test")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = provider._active_span_processor._span_processors[0].span_exporter
    yield exporter
    exporter.shutdown()
    mp.undo()


# ── OTel: configure creates a tracer and emits spans ────────────────────
def test_configure_otel_emits_span(memory_exporter: InMemorySpanExporter) -> None:
    """``configure_otel`` wires the global provider so ``tracer()`` emits spans."""
    memory_exporter.clear()

    with tracer().start_as_current_span("x"):
        pass

    trace.get_tracer_provider().force_flush()  # type: ignore[union-attr]
    spans = memory_exporter.get_finished_spans()

    assert len(spans) == 1
    assert spans[0].name == "x"
    assert spans[0].resource.attributes["service.name"] == "test"
    assert spans[0].resource.attributes["service.version"] == __version__


# ── OTel: add_span_attribute writes to current span ─────────────────────
def test_add_span_attribute_adds_to_current_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    """``add_span_attribute`` sets a key/value pair on the active span."""
    memory_exporter.clear()

    with tracer().start_as_current_span("x"):
        add_span_attribute("foo", "bar")

    trace.get_tracer_provider().force_flush()  # type: ignore[union-attr]
    spans = memory_exporter.get_finished_spans()

    assert len(spans) == 1
    assert spans[0].attributes == {"foo": "bar"}


# ── Metrics: exposition server serves recorded metrics ──────────────────
def test_metrics_server_exposes_recorded_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_metrics_server`` exposes counters and histograms on ``/metrics``."""
    from prometheus_client import start_http_server as real_start_http_server

    server_holder: list[Any] = []

    def _capturing_start_http_server(port: int) -> Any:
        httpd, thread = real_start_http_server(port)
        server_holder.append(httpd)
        return httpd, thread

    monkeypatch.setattr(
        "enten.metrics.start_http_server",
        _capturing_start_http_server,
    )

    port = _free_port()
    start_metrics_server(port)
    record_pr_review("passed")
    record_llm_call("gpt-4o-mini", 0.123)
    record_sandbox_exec("docker", 0.456)

    response = httpx.get(f"http://127.0.0.1:{port}/metrics")
    assert response.status_code == 200
    body = response.text
    assert "pr_reviews_total" in body
    assert 'result="passed"' in body
    assert "llm_call_duration_seconds" in body
    assert 'model="gpt-4o-mini"' in body
    assert "sandbox_exec_duration_seconds" in body
    assert 'sandbox="docker"' in body
    assert "pr_review_duration_seconds" in body

    server_holder[0].shutdown()
