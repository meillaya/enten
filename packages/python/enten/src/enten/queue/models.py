"""SQLAlchemy 2.0 declarative models for the job queue and review tables."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, NUMERIC, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from enten.db import Base

if TYPE_CHECKING:
    pass

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_generation: Mapped[int] = mapped_column(default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)


class PRReview(Base):
    __tablename__ = "pr_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    repo_id: Mapped[str] = mapped_column(Text)
    pr_number: Mapped[int] = mapped_column()
    commit_sha: Mapped[str] = mapped_column(Text)
    conclusion: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(NUMERIC(10, 6), default=Decimal("0"))
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.run_id", ondelete="SET NULL"), nullable=True
    )
    evidence: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    llm_calls: Mapped[list[LLMCall]] = relationship(back_populates="review")
    sandbox_runs: Mapped[list[SandboxRun]] = relationship(back_populates="review")


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reviews.id", ondelete="CASCADE"), nullable=True
    )
    model: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(NUMERIC(10, 6), default=Decimal("0"))
    duration_ms: Mapped[int] = mapped_column(default=0)
    tool_use_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    review: Mapped[PRReview] = relationship(back_populates="llm_calls")


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    repo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_findings: Mapped[list[JsonValue]] = mapped_column(JSONB, default=list)


class SandboxRun(Base):
    __tablename__ = "sandbox_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pr_reviews.id", ondelete="SET NULL"), nullable=True
    )
    sandbox_provider: Mapped[str] = mapped_column(Text)
    sandbox_id: Mapped[str] = mapped_column(Text)
    command: Mapped[str] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    evidence_paths: Mapped[list[JsonValue]] = mapped_column(JSONB, default=list)

    review: Mapped[PRReview] = relationship(back_populates="sandbox_runs")
