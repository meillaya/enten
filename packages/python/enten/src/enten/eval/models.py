from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class EvalMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    precision: float
    recall: float
    false_positive_rate: float
    count: int
    n_correct: int
    n_missed: int
    n_hallucinated: int


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n_fixtures: int
    aggregate: EvalMetrics
    by_kind: dict[str, EvalMetrics]
    cost: dict[str, Any]
    latency: dict[str, Any]
    determinism: dict[str, Any]
    baseline: dict[str, Any] | None
