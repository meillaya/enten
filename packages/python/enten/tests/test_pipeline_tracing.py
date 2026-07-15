from __future__ import annotations

from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enten.pipeline import InMemoryReviewStore, PipelineDependencies, execute_review
from enten.review.run import (
    EffectReceipt,
    GeneratedFindings,
    ReportPayload,
    ReviewRequest,
    VerificationEvidence,
)


async def test_pipeline_emits_observable_stage_spans_under_consumer(tmp_path: Path) -> None:
    # Given
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("pipeline-test")

    class Generator:
        async def generate(self, proposal: object) -> GeneratedFindings:
            _ = proposal
            return GeneratedFindings(findings=(), summary="clean")

    class Verifier:
        async def verify(self, request: object) -> VerificationEvidence:
            _ = request
            return VerificationEvidence(exit_codes=(0,), command=("true",), log_paths=())

    class Effects:
        async def publish(self, payload: ReportPayload) -> EffectReceipt:
            _ = payload
            return EffectReceipt(state="skipped")

    request = ReviewRequest(
        repository_id="local/repo",
        pr_number=1,
        head_sha="abc123",
        repository_path=tmp_path,
        public_repository=True,
        local_repository=True,
        verification_command=("true",),
    )
    dependencies = PipelineDependencies(
        store=InMemoryReviewStore(),
        generator=Generator(),
        verifier=Verifier(),
        effects=Effects(),
        tracer=tracer,
    )

    # When
    with tracer.start_as_current_span("pr_review.consume") as consumer:
        _ = await execute_review(request, dependencies)

    # Then
    spans = {span.name: span for span in exporter.get_finished_spans()}
    expected = {
        "pipeline.reserve",
        "pipeline.propose",
        "pipeline.findings",
        "pipeline.verify",
        "pipeline.persist",
        "pipeline.report",
    }
    assert expected <= spans.keys()
    consumer_id = consumer.get_span_context().span_id
    for name in expected:
        parent = spans[name].parent
        assert parent is not None
        assert parent.span_id == consumer_id
