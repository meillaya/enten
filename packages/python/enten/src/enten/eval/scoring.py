from __future__ import annotations

from typing import Any

from enten.eval.models import EvalMetrics
from enten.llm.types import Usage
from enten.review.generator import FindingSummary


def findings_match(
    bot: FindingSummary,
    expected: FindingSummary,
    *,
    line_tolerance: int = 2,
) -> bool:
    return bot.file == expected.file and abs(bot.line - expected.line) <= line_tolerance


def match_findings(
    bot_findings: list[FindingSummary],
    expected_findings: list[FindingSummary],
) -> tuple[int, int, int]:
    remaining_expected = list(expected_findings)
    n_correct = 0
    for bot in bot_findings:
        matched_idx = next(
            (
                idx
                for idx, expected in enumerate(remaining_expected)
                if findings_match(bot, expected)
            ),
            None,
        )
        if matched_idx is not None:
            n_correct += 1
            remaining_expected.pop(matched_idx)
    return n_correct, len(remaining_expected), len(bot_findings) - n_correct


def compute_metrics(
    kind: str,
    n_correct: int,
    n_missed: int,
    n_hallucinated: int,
) -> EvalMetrics:
    precision_denominator = n_correct + n_hallucinated
    recall_denominator = n_correct + n_missed
    precision = n_correct / precision_denominator if precision_denominator else 0.0
    recall = n_correct / recall_denominator if recall_denominator else 0.0
    false_positive_rate = (
        float(n_hallucinated)
        if kind == "no_false_positive"
        else n_hallucinated / precision_denominator
        if precision_denominator
        else 0.0
    )
    return EvalMetrics(
        precision=precision,
        recall=recall,
        false_positive_rate=false_positive_rate,
        count=recall_denominator,
        n_correct=n_correct,
        n_missed=n_missed,
        n_hallucinated=n_hallucinated,
    )


def collect_cost(latencies: list[float], usage: Usage) -> dict[str, Any]:
    return {
        "total_usd": 0.0,
        "total_tokens": usage.input_tokens + usage.output_tokens,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": int(sum(latencies)),
        "fixture_count": len(latencies),
    }
