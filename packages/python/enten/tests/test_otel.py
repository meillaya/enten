from __future__ import annotations

from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from enten import __version__
from enten.otel import add_span_attribute, configure_otel, tracer


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
