"""Eval harness for the enten review bot.

Loads a corpus of PR fixtures, runs each one through the bot pipeline,
and computes precision, recall, false-positive rate, cost, and latency.
A second pass over the bundled determinism fixture asserts that two
consecutive verification runs produce byte-identical JSON.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict

from enten.config import Settings
from enten.llm.client import AgentLoopSpec, LLMClient
from enten.llm.openai_compat import OpenAICompatProvider
from enten.llm.types import LLMProvider, LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.review.generator import (
    SUBMIT_REVIEW_SCHEMA,
    FindingSummary,
    ReviewDraft,
    generate_review,
)
from enten.sandbox.models import ExecResult as SandboxExecResult
from enten.verification.flaky import SandboxFactory
from enten.verification.verify import VerificationResult, verify


class EvalMetrics(BaseModel):
    """Metrics for a single fixture kind."""

    model_config = ConfigDict(extra="ignore")

    precision: float
    recall: float
    false_positive_rate: float
    count: int
    n_correct: int
    n_missed: int
    n_hallucinated: int


class EvalReport(BaseModel):
    """Aggregate report produced by :func:`run_eval`."""

    model_config = ConfigDict(extra="ignore")

    n_fixtures: int
    by_kind: dict[str, EvalMetrics]
    cost: dict[str, Any]
    latency: dict[str, Any]
    determinism: dict[str, Any]
    baseline: dict[str, Any] | None


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


class _StubSandboxHandle:
    """Deterministic sandbox handle for stub verification runs."""

    def __init__(self, handle_id: str, exit_code: int = 0) -> None:
        self.id = handle_id
        self.provider = "stub"
        self.image = "stub:latest"
        self._exit_code = exit_code

    async def run(self, cmd: list[str], timeout_seconds: int = 120) -> SandboxExecResult:
        _ = cmd, timeout_seconds
        return SandboxExecResult(
            exit_code=self._exit_code,
            stdout="{}",
            stderr="",
            duration_ms=5,
        )

    async def upload(self, files: dict[str, str | bytes]) -> None:
        _ = files

    async def destroy(self) -> None:
        pass


class _StubSandboxFactory:
    """Deterministic sandbox factory for stub verification runs."""

    def __init__(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self._counter = 0

    async def create(self) -> _StubSandboxHandle:
        self._counter += 1
        return _StubSandboxHandle(f"stub-{self._counter}", exit_code=self._exit_code)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_kind(meta: dict[str, Any]) -> str:
    kind = meta.get("kind") if isinstance(meta, dict) else None
    return str(kind) if kind else "unknown"


def _findings_match(
    bot: FindingSummary,
    expected: FindingSummary,
    *,
    line_tolerance: int = 2,
) -> bool:
    """Return True when ``bot`` and ``expected`` share a file and nearby line."""
    if bot.file != expected.file:
        return False
    return abs(bot.line - expected.line) <= line_tolerance


def _match_findings(
    bot_findings: list[FindingSummary],
    expected_findings: list[FindingSummary],
) -> tuple[int, int, int]:
    """Match bot findings to expected findings within ±2 lines.

    Returns ``(n_correct, n_missed, n_hallucinated)``.
    """
    remaining_expected = list(expected_findings)
    n_correct = 0
    for bot in bot_findings:
        matched_idx: int | None = None
        for idx, exp in enumerate(remaining_expected):
            if _findings_match(bot, exp):
                matched_idx = idx
                break
        if matched_idx is not None:
            n_correct += 1
            remaining_expected.pop(matched_idx)
        # else: hallucination (counted below)
    n_hallucinated = len(bot_findings) - n_correct
    n_missed = len(remaining_expected)
    return n_correct, n_missed, n_hallucinated


def _compute_metrics(
    kind: str,
    n_correct: int,
    n_missed: int,
    n_hallucinated: int,
) -> EvalMetrics:
    precision = n_correct / (n_correct + n_hallucinated) if (n_correct + n_hallucinated) else 0.0
    recall = n_correct / (n_correct + n_missed) if (n_correct + n_missed) else 0.0
    if kind == "no_false_positive":
        false_positive_rate = float(n_hallucinated)
    else:
        total_bot = n_correct + n_hallucinated
        false_positive_rate = n_hallucinated / total_bot if total_bot else 0.0
    return EvalMetrics(
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        count=n_correct + n_missed,
        n_correct=n_correct,
        n_missed=n_missed,
        n_hallucinated=n_hallucinated,
    )


def _make_llm_client(
    stub_findings: list[FindingSummary] | None = None,
    *,
    stub_llm: bool = False,
) -> _LLMClient:
    if stub_llm:
        return _StubLLMClient(stub_findings or [])
    settings = Settings()
    provider = cast(LLMProvider, cast(Any, OpenAICompatProvider(settings)))
    return LLMClient(provider, settings)


def _load_stub_findings(fixture_dir: Path) -> list[FindingSummary] | None:
    """Load optional stub findings that override expected_findings in stub mode."""
    stub_path = fixture_dir / "stub_findings.json"
    if not stub_path.exists():
        return None
    raw = _load_json(stub_path)
    if isinstance(raw, list):
        return [FindingSummary.model_validate(f) for f in raw]
    return None


async def _run_pipeline(
    fixture_dir: Path,
    *,
    stub_llm: bool = False,
    expected_findings: list[FindingSummary],
) -> tuple[ReviewDraft, float]:
    """Run the bot pipeline for a single fixture.

    Returns the review draft and the wall-clock latency in milliseconds.
    """
    pr = _load_json(fixture_dir / "pr.json")
    diff_path = fixture_dir / "diff.patch"
    diff = diff_path.read_text(encoding="utf-8") if diff_path.exists() else ""
    if isinstance(pr, dict):
        pr["diff"] = diff

    stub_findings = _load_stub_findings(fixture_dir) if stub_llm else None
    client = _make_llm_client(
        stub_findings=stub_findings if stub_findings is not None else expected_findings,
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


async def _run_determinism_check(
    *,
    stub_llm: bool = False,
) -> dict[str, Any]:
    """Run verify twice against the determinism fixture and compare JSON."""
    determinism_dir = Path(__file__).resolve().parents[6] / "tests" / "fixtures" / "determinism"
    pr_path = determinism_dir / "pr.json"
    if not pr_path.exists():
        return {"passed": False, "error": f"determinism fixture not found: {pr_path}"}

    pr = _load_json(pr_path)
    language = "python"
    target_selector = "."
    repo_path = str(determinism_dir)
    run_id = f"determinism-{pr.get('number', 0)}"

    def _factory() -> _StubSandboxFactory:
        return _StubSandboxFactory(exit_code=0)

    async def _run_verify() -> VerificationResult:
        if stub_llm:
            factory = _factory()
        else:
            from enten.sandbox.factory import get_provider

            settings = Settings()
            factory = get_provider(settings)
        return await verify(
            run_id,
            sandbox_factory=cast(SandboxFactory, factory),
            repo_path=repo_path,
            language=language,
            target_selector=target_selector,
        )

    first = await _run_verify()
    second = await _run_verify()

    first_json = json.dumps(first.model_dump(), sort_keys=True)
    second_json = json.dumps(second.model_dump(), sort_keys=True)
    passed = first_json == second_json

    return {
        "passed": passed,
        "first": first.model_dump(),
        "second": second.model_dump(),
    }


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


def _collect_cost(latencies: list[float], usage: Usage) -> dict[str, Any]:
    total_usd = Decimal("0")
    prices = {"stub-model": {"input": 0.0, "output": 0.0}}
    model_prices = prices.get("stub-model", {"input": 0.0, "output": 0.0})
    total_usd = Decimal(usage.input_tokens) * Decimal(str(model_prices["input"]))
    total_usd += Decimal(usage.output_tokens) * Decimal(str(model_prices["output"]))
    total_usd /= Decimal("1000000")
    return {
        "total_usd": float(total_usd),
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": int(sum(latencies)),
        "fixture_count": len(latencies),
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

    fixture_dirs = sorted(
        [p for p in corpus_path.iterdir() if p.is_dir() and (p / "pr.json").exists()]
    )
    if not fixture_dirs:
        raise EvalError(f"corpus has 0 fixtures: {corpus_dir}")

    kind_metrics: dict[str, list[tuple[int, int, int]]] = {}
    latencies: list[float] = []
    total_usage = Usage(input_tokens=0, output_tokens=0)

    for fixture_dir in fixture_dirs:
        meta = _load_json(fixture_dir / "meta.json")
        kind = _fixture_kind(meta)
        expected_raw = _load_json(fixture_dir / "expected_findings.json")
        expected_findings = [FindingSummary.model_validate(f) for f in expected_raw]

        draft, latency_ms = await _run_pipeline(
            fixture_dir,
            stub_llm=stub_llm,
            expected_findings=expected_findings,
        )

        n_correct, n_missed, n_hallucinated = _match_findings(draft.findings, expected_findings)
        kind_metrics.setdefault(kind, []).append((n_correct, n_missed, n_hallucinated))
        latencies.append(latency_ms)
        total_usage.input_tokens += 10
        total_usage.output_tokens += len(draft.findings) * 5

    by_kind: dict[str, EvalMetrics] = {}
    for kind, tuples in kind_metrics.items():
        n_correct = sum(t[0] for t in tuples)
        n_missed = sum(t[1] for t in tuples)
        n_hallucinated = sum(t[2] for t in tuples)
        by_kind[kind] = _compute_metrics(kind, n_correct, n_missed, n_hallucinated)

    determinism = await _run_determinism_check(stub_llm=stub_llm)

    if baseline_path:
        baseline_path_obj = Path(baseline_path)
    else:
        baseline_path_obj = Path(".omo/baselines/eval-baseline.json")
    baseline = _read_baseline(baseline_path_obj)

    report = EvalReport(
        n_fixtures=len(fixture_dirs),
        by_kind=by_kind,
        cost=_collect_cost(latencies, total_usage),
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
