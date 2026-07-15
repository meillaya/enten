"""Canonical value objects for one deterministic pull-request review run."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewKey = tuple[str, int, str]
ReviewStage = Literal[
    "created",
    "proposed",
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
    correlation_run_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_public_remote_repository(self) -> ReviewRequest:
        if not self.local_repository and not self.public_repository:
            raise ValueError("only public remote repositories can be reviewed")
        return self

    @property
    def key(self) -> ReviewKey:
        return (self.repository_id, self.pr_number, self.head_sha)


class Proposal(BaseModel):
    """Bounded repository evidence supplied to the findings generator."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    repository_path: Path
    files: tuple[str, ...] = Field(max_length=2_000)
    verification_command: tuple[str, ...]


class Finding(BaseModel):
    """A bounded finding anchored to repository evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    category: str = Field(min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    message: str = Field(min_length=1, max_length=1_000)
    file: str = Field(min_length=1, max_length=500)
    line: int = Field(gt=0)


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
    findings: GeneratedFindings | None = None
    verification: VerificationEvidence | None = None
    report_receipt: EffectReceipt | None = None


class PipelineResult(BaseModel):
    """Pipeline return value distinguishing fresh execution from replay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    run: ReviewRun
    replayed: bool


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


__all__ = [
    "EffectReceipt",
    "Finding",
    "GeneratedFindings",
    "PipelineResult",
    "Proposal",
    "ReportPayload",
    "ReviewKey",
    "ReviewRequest",
    "ReviewRun",
    "ReviewStage",
    "Verdict",
    "VerificationEvidence",
]
