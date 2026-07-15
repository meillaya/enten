from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from enten.review.run import ReviewRequest


class EnvelopeRepository(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(gt=0)
    full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$", max_length=255)


class EnvelopePullRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    number: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")


class EnvelopeActor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)


class ReviewJobEnvelope(BaseModel):
    """Typed Python boundary for the shared TypeScript PR job envelope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"]
    installation_id: int = Field(gt=0)
    delivery_id: UUID
    repository: EnvelopeRepository
    pull_request: EnvelopePullRequest
    action: Literal["opened", "reopened", "synchronize", "ready_for_review"]
    actor: EnvelopeActor
    review_contract: str = Field(pattern=r"^[0-9a-f]{64}$")
    traceparent: str = Field(pattern=r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
    tracestate: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("traceparent")
    @classmethod
    def reject_invalid_trace_identifiers(cls, value: str) -> str:
        version, trace_id, parent_id, _flags = value.split("-")
        if version == "ff" or set(trace_id) == {"0"} or set(parent_id) == {"0"}:
            raise ValueError("traceparent contains a forbidden identifier")
        return value

    def to_request(
        self,
        *,
        repository_path: Path,
        verification_command: tuple[str, ...] | None = None,
        expected_review_contract: str | None = None,
        correlation_run_id: str | None = None,
    ) -> ReviewRequest:
        command = verification_command or ("python", "-m", "compileall", "-q", ".")
        if (
            expected_review_contract is not None
            and self.review_contract != expected_review_contract
        ):
            raise ValueError("job review contract does not match the worker verifier configuration")
        return ReviewRequest(
            repository_id=str(self.repository.id),
            repository_full_name=self.repository.full_name,
            pr_number=self.pull_request.number,
            head_sha=self.pull_request.head_sha,
            repository_path=repository_path,
            public_repository=True,
            local_repository=False,
            verification_command=command,
            review_contract=self.review_contract,
            correlation_run_id=correlation_run_id,
        )


__all__ = ["ReviewJobEnvelope"]
