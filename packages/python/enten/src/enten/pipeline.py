from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, assert_never

from opentelemetry import trace

from enten.reporting.payload import build_report
from enten.review.diff import DiffIndex
from enten.review.envelope import ReviewJobEnvelope
from enten.review.local import discover_source_files
from enten.review.python_probes import ProbeRunner, build_findings
from enten.review.run import (
    EffectReceipt,
    GeneratedFindings,
    HypothesisSet,
    PipelineResult,
    Proposal,
    ReportPayload,
    ReviewKey,
    ReviewRequest,
    ReviewRun,
    ReviewStage,
    VerificationEvidence,
)
from enten.review.store import InMemoryReviewStore, JsonReviewStore, ReviewStore


class FindingsGenerator(Protocol):
    async def generate(self, proposal: Proposal) -> GeneratedFindings: ...


class HypothesisGenerator(Protocol):
    async def generate(self, proposal: Proposal) -> HypothesisSet: ...


class ReviewVerifier(Protocol):
    async def verify(self, request: ReviewRequest) -> VerificationEvidence: ...


class ReviewEffects(Protocol):
    async def publish(self, payload: ReportPayload) -> EffectReceipt: ...

    async def reconcile(self, payload: ReportPayload) -> EffectReceipt: ...


class StageSink(Protocol):
    async def transition(self, stage: ReviewStage) -> None: ...


class NullStageSink:
    async def transition(self, stage: ReviewStage) -> None:
        _ = stage


@dataclass(frozen=True, slots=True)
class PipelineDependencies:
    store: ReviewStore
    generator: FindingsGenerator
    verifier: ReviewVerifier
    effects: ReviewEffects
    hypothesis_generator: HypothesisGenerator | None = None
    probe_runner: ProbeRunner | None = None
    probe_publish: bool = True
    stage_sink: StageSink = NullStageSink()
    tracer: trace.Tracer = trace.get_tracer(__name__)


class IndeterminateEffectError(RuntimeError):
    """An outbound attempt may have succeeded and therefore cannot be repeated."""


async def execute_review(
    request: ReviewRequest,
    dependencies: PipelineDependencies,
) -> PipelineResult:
    with dependencies.tracer.start_as_current_span("pipeline.reserve"):
        existing = await dependencies.store.get_by_key(request.key)
        reconcile_completed_checkpoint = existing is not None and existing.stage == "completed"
        run = existing or ReviewRun(
            review_id=_review_id(request.key),
            request=request,
            stage="created",
        )
        if existing is None:
            await _persist(dependencies, run)

    while True:
        match run.stage:
            case "created":
                with dependencies.tracer.start_as_current_span("pipeline.propose"):
                    proposal = Proposal(
                        repository_path=request.repository_path,
                        files=discover_source_files(request.repository_path),
                        verification_command=request.verification_command,
                        pr_number=request.pr_number,
                        correlation_run_id=request.correlation_run_id,
                        diff=request.diff,
                        changed=DiffIndex.from_patch(request.diff),
                    )
                    run = run.model_copy(update={"stage": "proposed", "proposal": proposal})
                    await _persist(dependencies, run)
            case "proposed":
                if (
                    dependencies.hypothesis_generator is not None
                    and dependencies.probe_runner is not None
                ):
                    if run.proposal is None:
                        raise ValueError("proposed review is missing its proposal")
                    proposal = run.proposal.model_copy(
                        update={"repository_path": request.repository_path}
                    )
                    run = run.model_copy(update={"stage": "hypothesized", "proposal": proposal})
                    await _persist(dependencies, run)
                else:
                    with dependencies.tracer.start_as_current_span("pipeline.findings"):
                        if run.proposal is None:
                            raise ValueError("proposed review is missing its proposal")
                        proposal = run.proposal.model_copy(
                            update={"repository_path": request.repository_path}
                        )
                        findings = await dependencies.generator.generate(proposal)
                        run = run.model_copy(
                            update={
                                "stage": "findings",
                                "proposal": proposal,
                                "findings": findings,
                            }
                        )
                        await _persist(dependencies, run)
            case "hypothesized":
                with dependencies.tracer.start_as_current_span("pipeline.hypothesize"):
                    if dependencies.hypothesis_generator is None or run.proposal is None:
                        raise ValueError(
                            "hypothesized review has no hypothesis generator or proposal"
                        )
                    hypotheses = await dependencies.hypothesis_generator.generate(run.proposal)
                    run = run.model_copy(
                        update={"stage": "probing", "hypotheses": hypotheses}
                    )
                    await _persist(dependencies, run)
            case "probing":
                with dependencies.tracer.start_as_current_span("pipeline.probe"):
                    if (
                        dependencies.probe_runner is None
                        or run.proposal is None
                        or run.hypotheses is None
                    ):
                        raise ValueError("probing review is missing its runner or hypotheses")
                    probes = await dependencies.probe_runner.run(run.hypotheses, run.proposal)
                    findings = build_findings(run.hypotheses, probes)
                    if not dependencies.probe_publish:
                        findings = findings.model_copy(
                            update={
                                "findings": (),
                                "summary": (
                                    f"{len(probes.results)} probes measured; "
                                    "findings withheld in shadow mode"
                                ),
                            }
                        )
                    run = run.model_copy(
                        update={"stage": "findings", "probes": probes, "findings": findings}
                    )
                    await _persist(dependencies, run)
            case "findings":
                with dependencies.tracer.start_as_current_span("pipeline.verify"):
                    evidence = await dependencies.verifier.verify(request)
                    if evidence.infrastructure_detail is None:
                        evidence = evidence.model_copy(
                            update={"verdict": _arbitrate(evidence.exit_codes)}
                        )
                    run = run.model_copy(update={"stage": "verified", "verification": evidence})
                    await _persist(dependencies, run)
            case "verified":
                with dependencies.tracer.start_as_current_span("pipeline.persist"):
                    run = run.model_copy(update={"stage": "persisted"})
                    await _persist(dependencies, run)
            case "persisted":
                with dependencies.tracer.start_as_current_span("pipeline.report"):
                    attempting = EffectReceipt(state="attempting")
                    run = run.model_copy(
                        update={"stage": "reporting", "report_receipt": attempting}
                    )
                    await _persist(dependencies, run)
                    try:
                        receipt = await dependencies.effects.publish(build_report(run))
                    except Exception as exc:
                        indeterminate = EffectReceipt(
                            state="indeterminate",
                            detail=f"{type(exc).__name__}: {exc}",
                        )
                        run = run.model_copy(update={"report_receipt": indeterminate})
                        await _persist(dependencies, run)
                        raise IndeterminateEffectError(indeterminate.detail) from exc
                    if receipt.state not in {"posted", "skipped"}:
                        run = run.model_copy(update={"report_receipt": receipt})
                        await _persist(dependencies, run)
                        raise IndeterminateEffectError(
                            receipt.detail or "outbound effect is indeterminate"
                        )
                    run = run.model_copy(update={"stage": "completed", "report_receipt": receipt})
                    await _persist(dependencies, run)
            case "reporting":
                try:
                    with dependencies.tracer.start_as_current_span("pipeline.reconcile"):
                        receipt = await dependencies.effects.reconcile(build_report(run))
                except Exception as exc:
                    raise IndeterminateEffectError(
                        f"effect reconciliation failed: {type(exc).__name__}: {exc}"
                    ) from exc
                if receipt.state in {"posted", "skipped"}:
                    run = run.model_copy(update={"stage": "completed", "report_receipt": receipt})
                    await _persist(dependencies, run)
                    continue
                run = run.model_copy(update={"report_receipt": receipt})
                await _persist(dependencies, run)
                raise IndeterminateEffectError(receipt.detail or "outbound effect is indeterminate")
            case "completed":
                if reconcile_completed_checkpoint:
                    await dependencies.stage_sink.transition("completed")
                return PipelineResult(run=run, replayed=existing is not None)
            case unreachable:
                assert_never(unreachable)


async def summarize_cost(
    store: ReviewStore,
    *,
    pr_number: int | None = None,
    repository_id: str | None = None,
) -> dict[str, int | str]:
    reviews = await store.list_reviews(
        pr_number=pr_number,
        repository_id=repository_id,
    )
    findings = [run.findings for run in reviews if run.findings is not None]
    return {
        "reviews": len(reviews),
        "tokens_in": sum(item.tokens_in for item in findings),
        "tokens_out": sum(item.tokens_out for item in findings),
        "cost_usd": str(sum((item.cost_usd for item in findings), start=Decimal("0"))),
    }


async def _persist(dependencies: PipelineDependencies, run: ReviewRun) -> None:
    await dependencies.store.save(run)
    await dependencies.stage_sink.transition(run.stage)


def _review_id(key: ReviewKey) -> str:
    raw = f"{key[0]}\x00{key[1]}\x00{key[2]}\x00{key[3]}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _arbitrate(exit_codes: tuple[int, ...]) -> str:
    pass_count = sum(code == 0 for code in exit_codes)
    if pass_count == len(exit_codes):
        return "pass"
    if pass_count * 3 >= len(exit_codes) * 2:
        return "flaky"
    return "fail"


__all__ = [
    "FindingsGenerator",
    "InMemoryReviewStore",
    "IndeterminateEffectError",
    "JsonReviewStore",
    "PipelineDependencies",
    "ReviewEffects",
    "ReviewJobEnvelope",
    "ReviewStore",
    "ReviewVerifier",
    "StageSink",
    "execute_review",
    "summarize_cost",
]
