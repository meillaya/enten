"""Canonical value objects for one deterministic pull-request review run."""

from __future__ import annotations

import shlex
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enten.review.contract import review_contract_digest
from enten.review.diff import MAX_DIFF_BYTES, DiffIndex

ReviewKey = tuple[str, int, str, str]
ReviewStage = Literal[
    "created",
    "proposed",
    "hypothesized",
    "probing",
    "findings",
    "verified",
    "persisted",
    "reporting",
    "completed",
]
Verdict = Literal["pass", "flaky", "fail", "error"]


class ReviewRequest(BaseModel):
    """Parsed review boundary shared by webhook workers and the local CLI."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    repository_id: str = Field(min_length=1, max_length=255)
    repository_full_name: str | None = Field(default=None, max_length=255)
    pr_number: int = Field(ge=0)
    head_sha: str = Field(min_length=1, max_length=64)
    repository_path: Path
    public_repository: bool = True
    local_repository: bool = True
    verification_command: tuple[str, ...] = Field(min_length=1)
    review_contract: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    correlation_run_id: str | None = Field(default=None, max_length=255)
    diff: str = Field(default="", max_length=MAX_DIFF_BYTES)

    @property
    def key(self) -> ReviewKey:
        contract = self.review_contract or review_contract_digest(
            shlex.join(self.verification_command),
            "local-process",
        )
        return (self.repository_id, self.pr_number, self.head_sha, contract)


class Proposal(BaseModel):
    """Bounded repository evidence supplied to the findings generator."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    repository_path: Path
    files: tuple[str, ...] = Field(max_length=2_000)
    verification_command: tuple[str, ...]
    pr_number: int = 0
    correlation_run_id: str | None = None
    diff: str = Field(default="", max_length=MAX_DIFF_BYTES)
    changed: DiffIndex = Field(default_factory=DiffIndex)


class FindingEvidence(BaseModel):
    """Executable evidence attached to a confirmed finding."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    probe_command: tuple[str, ...]
    test_source: str = Field(min_length=1, max_length=20_000)
    exit_code: int
    output_excerpt: str = Field(default="", max_length=4_000)
    test_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox: str = Field(default="docker", max_length=20)


class Finding(BaseModel):
    """A bounded finding anchored to repository evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    category: str = Field(min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    message: str = Field(min_length=1, max_length=1_000)
    file: str = Field(min_length=1, max_length=500)
    line: int = Field(gt=0)
    start_line: int | None = Field(default=None, gt=0)
    side: Literal["RIGHT"] = "RIGHT"
    evidence: FindingEvidence | None = None


class Hypothesis(BaseModel):
    """A model claim plus a pytest probe that can confirm or reject it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    message: str = Field(min_length=1, max_length=1_000)
    file: str = Field(min_length=1, max_length=500)
    line: int = Field(gt=0)
    rationale: str = Field(default="", max_length=2_000)
    test_source: str = Field(min_length=1, max_length=20_000)
    test_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HypothesisSet(BaseModel):
    """Bounded model output before any probe is executed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    hypotheses: tuple[Hypothesis, ...] = Field(max_length=3)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)


ProbeStatus = Literal["confirmed", "rejected", "inconclusive"]


class ProbeResult(BaseModel):
    """Result of running one generated probe in a sandbox."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=64)
    status: ProbeStatus
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    output_excerpt: str = Field(default="", max_length=4_000)
    test_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProbeEvidence(BaseModel):
    """All probe results for a review run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    results: tuple[ProbeResult, ...] = Field(default=(), max_length=3)


class GeneratedFindings(BaseModel):
    """LLM/local-analysis output plus the cost charged for producing it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    findings: tuple[Finding, ...] = Field(max_length=100)
    summary: str = Field(default="", max_length=4_000)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)


class VerificationEvidence(BaseModel):
    """Actual process exits consumed by the closed-form arbiter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    exit_codes: tuple[int, ...] = Field(max_length=10)
    command: tuple[str, ...]
    log_paths: tuple[str, ...] = Field(max_length=20)
    verdict: Verdict = "error"
    infrastructure_detail: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def require_process_or_infrastructure_evidence(self) -> VerificationEvidence:
        if not self.exit_codes and self.infrastructure_detail is None:
            raise ValueError("verification requires process exits or infrastructure detail")
        if self.infrastructure_detail is not None and self.verdict != "error":
            raise ValueError("infrastructure failures require an error verdict")
        return self


class EffectReceipt(BaseModel):
    """Durable result or ambiguity marker for outbound GitHub effects."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    state: Literal["attempting", "posted", "skipped", "indeterminate"]
    check_run_id: str | None = Field(default=None, max_length=100)
    comment_id: str | None = Field(default=None, max_length=100)
    check_run_url: str | None = Field(default=None, max_length=2_000)
    comment_url: str | None = Field(default=None, max_length=2_000)
    detail: str | None = Field(default=None, max_length=1_000)


class ReviewRun(BaseModel):
    """The single artifact updated at every review stage."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    review_id: str = Field(min_length=1, max_length=64)
    request: ReviewRequest
    stage: ReviewStage
    proposal: Proposal | None = None
    hypotheses: HypothesisSet | None = None
    probes: ProbeEvidence | None = None
    findings: GeneratedFindings | None = None
    verification: VerificationEvidence | None = None
    report_receipt: EffectReceipt | None = None


class PipelineResult(BaseModel):
    """Pipeline return value distinguishing fresh execution from replay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    run: ReviewRun
    replayed: bool


class ReportAnnotation(BaseModel):
    """A file/line annotation to attach to the check run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(gt=0)
    end_line: int = Field(gt=0)
    annotation_level: Literal["notice", "warning", "failure"]
    title: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1_000)


class ReportPayload(BaseModel):
    """GitHub-bound fields after length and file:line enforcement."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    owner: str | None = Field(default=None, max_length=100)
    repo: str | None = Field(default=None, max_length=100)
    pr_number: int = Field(ge=0)
    head_sha: str = Field(max_length=64)
    conclusion: Literal["success", "failure", "neutral"]
    title: str = Field(max_length=80)
    summary: str = Field(max_length=65_535)
    comment: str = Field(max_length=65_535)
    annotations: tuple[ReportAnnotation, ...] = Field(default=(), max_length=50)


__all__ = [
    "EffectReceipt",
    "Finding",
    "FindingEvidence",
    "Hypothesis",
    "HypothesisSet",
    "ProbeEvidence",
    "ProbeResult",
    "ProbeStatus",
    "GeneratedFindings",
    "PipelineResult",
    "Proposal",
    "ReportAnnotation",
    "ReportPayload",
    "ReviewKey",
    "ReviewRequest",
    "ReviewRun",
    "ReviewStage",
    "Verdict",
    "VerificationEvidence",
]
