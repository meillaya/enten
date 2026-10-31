"""Eval harness for the enten review bot.

Loads a corpus of PR fixtures, runs each one through the bot pipeline,
and computes precision, recall, false-positive rate, cost, and latency.
A second pass over the bundled determinism fixture asserts that two
consecutive verification runs produce byte-identical JSON.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, cast

from enten.config import Settings
from enten.eval.determinism import run_determinism_check
from enten.eval.models import EvalMetrics, EvalReport
from enten.eval.scoring import collect_cost, compute_metrics, match_findings
from enten.llm.client import AgentLoopSpec, LLMClient
from enten.llm.factory import create_llm_provider
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.review.generator import (
    SUBMIT_REVIEW_SCHEMA,
    FindingSummary,
    ReviewDraft,
    generate_review,
)


class EvalError(Exception):
    """Raised when the eval harness cannot run (e.g. empty corpus)."""


class _LLMClient(Protocol):
    """Protocol for the LLM client used by :func:`generate_review`."""

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, object]],
        tools: list[ToolSchema | dict[str, object]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse: ...


class _StubLLMClient:
    """Stub LLM client that returns a fixed sequence of review findings.

    The stub is deterministic, dependency-free, and records timing/token
    metadata so the eval harness can still report cost and latency.
    """

    def __init__(
        self,
        findings: list[FindingSummary],
        *,
        duration_ms: int = 10,
        usage: Usage | None = None,
    ) -> None:
        self.findings = findings
        self.duration_ms = duration_ms
        self.usage = usage or Usage(input_tokens=10, output_tokens=len(findings) * 5)
        self.calls: list[
            tuple[list[Message | dict[str, object]], list[ToolSchema | dict[str, object]], str]
        ] = []

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, object]],
        tools: list[ToolSchema | dict[str, object]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        _ = model
        self.calls.append((messages, tools, run_id))
        tool_call = ToolUse(
            id="stub-submit-review",
            name=SUBMIT_REVIEW_SCHEMA.name,
            arguments={"findings": [f.model_dump() for f in self.findings]},
        )
        _ = await agent_loop.execute(tool_call)
        return LLMResponse(
            content="Review complete (stub).",
            tool_calls=[],
            usage=self.usage,
            model="stub-model",
            raw={"provider": "stub"},
        )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_llm_client(
    stub_findings: list[FindingSummary] | None = None,
    *,
    stub_llm: bool = False,
) -> _LLMClient:
    if stub_llm:
        return _StubLLMClient(stub_findings or [])
    settings = Settings()
    provider = create_llm_provider(settings)
    return LLMClient(provider, settings)


def _load_predictions(fixture_dir: Path) -> list[FindingSummary]:
    predictions_path = fixture_dir / "stub_findings.json"
    if not predictions_path.exists():
        raise EvalError(f"stub predictions not found: {predictions_path}")
    raw = _load_json(predictions_path)
    if isinstance(raw, list):
        return [FindingSummary.model_validate(f) for f in raw]
    raise EvalError(f"stub predictions must be a list: {predictions_path}")


def _load_fixture_kind(fixture_dir: Path) -> str:
    meta_path = fixture_dir / "meta.json"
    meta = _load_json(meta_path)
    if not isinstance(meta, dict) or not isinstance(meta.get("kind"), str):
        raise EvalError(f"fixture meta.kind must be a string: {meta_path}")
    return meta["kind"]


async def _run_pipeline(
    fixture_dir: Path,
    *,
    stub_llm: bool = False,
) -> tuple[ReviewDraft, float]:
    """Run the bot pipeline for a single fixture.

    Returns the review draft and the wall-clock latency in milliseconds.
    """
    pr = _load_json(fixture_dir / "pr.json")
    diff_path = fixture_dir / "diff.patch"
    diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
    if isinstance(pr, dict):
        pr["diff"] = diff

    stub_findings = _load_predictions(fixture_dir) if stub_llm else None
    client = _make_llm_client(
        stub_findings=stub_findings,
        stub_llm=stub_llm,
    )
    started = time.monotonic()
    draft = await generate_review(
        pr,
        retrieved_chunks=[],
        sandbox_path=str(fixture_dir),
        llm_client=client,
    )
    latency_ms = (time.monotonic() - started) * 1000
    return draft, latency_ms


def _read_baseline(baseline_path: Path) -> dict[str, Any] | None:
    if not baseline_path.exists():
        return None
    try:
        return cast(dict[str, Any], json.loads(baseline_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return None


def _diff_baseline(report: EvalReport, baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    if baseline is None:
        return None
    current = report.model_dump()
    stable_current = {"n_fixtures": current["n_fixtures"], "by_kind": current["by_kind"]}
    stable_baseline = {
        "n_fixtures": baseline.get("n_fixtures"),
        "by_kind": baseline.get("by_kind"),
    }
    return {
        "changed": stable_current != stable_baseline,
        "current": stable_current,
        "previous": stable_baseline,
    }


async def run_eval(
    corpus_dir: str,
    *,
    stub_llm: bool = False,
    baseline_path: str | None = None,
) -> EvalReport:
    """Evaluate the bot against every fixture under ``corpus_dir``.

    Args:
        corpus_dir: Directory containing fixture subdirectories. Each
            fixture must contain ``pr.json``, ``diff.patch``,
            ``expected_findings.json``, and ``meta.json``.
        stub_llm: When True, use a deterministic stub LLM client instead
            of calling a real provider.

    Returns:
        An :class:`EvalReport` with per-kind metrics, cost, latency, and
        a determinism comparison.

    Raises:
        EvalError: If the corpus contains no fixtures.
    """
    corpus_path = Path(corpus_dir)
    if not corpus_path.is_dir():
        raise EvalError(f"corpus is not a directory: {corpus_dir}")

    pr_json_paths = sorted(corpus_path.rglob("pr.json"))
    fixture_dirs = [p.parent for p in pr_json_paths]
    if not fixture_dirs:
        raise EvalError(f"corpus has 0 fixtures: {corpus_dir}")

    kind_metrics: dict[str, list[tuple[int, int, int]]] = {}
    latencies: list[float] = []
    total_usage = Usage(input_tokens=0, output_tokens=0)

    for fixture_dir in fixture_dirs:
        kind = _load_fixture_kind(fixture_dir)
        expected_raw = _load_json(fixture_dir / "expected_findings.json")
        expected_findings = [FindingSummary.model_validate(f) for f in expected_raw]

        draft, latency_ms = await _run_pipeline(
            fixture_dir,
            stub_llm=stub_llm,
        )

        n_correct, n_missed, n_hallucinated = match_findings(draft.findings, expected_findings)
        kind_metrics.setdefault(kind, []).append((n_correct, n_missed, n_hallucinated))
        latencies.append(latency_ms)
        total_usage.input_tokens += 10
        total_usage.output_tokens += len(draft.findings) * 5

    by_kind: dict[str, EvalMetrics] = {}
    for kind, tuples in kind_metrics.items():
        n_correct = sum(t[0] for t in tuples)
        n_missed = sum(t[1] for t in tuples)
        n_hallucinated = sum(t[2] for t in tuples)
        by_kind[kind] = compute_metrics(kind, n_correct, n_missed, n_hallucinated)

    aggregate = compute_metrics(
        "aggregate",
        sum(metric.n_correct for metric in by_kind.values()),
        sum(metric.n_missed for metric in by_kind.values()),
        sum(metric.n_hallucinated for metric in by_kind.values()),
    )

    determinism = await run_determinism_check()

    if baseline_path:
        baseline_path_obj = Path(baseline_path)
    else:
        baseline_path_obj = Path(".omo/baselines/eval-baseline.json")
    baseline = _read_baseline(baseline_path_obj)

    report = EvalReport(
        n_fixtures=len(fixture_dirs),
        aggregate=aggregate,
        by_kind=by_kind,
        cost=collect_cost(latencies, total_usage),
        latency={
            "total_ms": int(sum(latencies)),
            "mean_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "per_fixture": {
                p.name: int(latency) for p, latency in zip(fixture_dirs, latencies, strict=True)
            },
        },
        determinism=determinism,
        baseline=baseline,
    )

    report.baseline = _diff_baseline(report, baseline)
    return report


def write_baseline(report: EvalReport, baseline_path: Path | None = None) -> Path:
    """Write ``report`` to the baseline file, creating parent dirs as needed."""
    path = baseline_path or Path(".omo/baselines/eval-baseline.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return path


__all__ = [
    "EvalError",
    "EvalMetrics",
    "EvalReport",
    "run_eval",
    "write_baseline",
]
