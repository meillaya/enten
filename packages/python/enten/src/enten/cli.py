"""enten CLI entry point.

Exposes the top-level ``enten`` command tree including the eval harness
(T37), worker loop, and future plan/exec dispatch.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from enten.eval import EvalReport

app = typer.Typer(
    name="enten",
    help="enten - AI engineering agent.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def main() -> None:
    """Print the canonical greeting. Placeholder for the real CLI tree."""
    typer.echo("enten")


@app.command()
def worker(poll_interval: int = 5) -> None:
    """Run the enten worker process."""
    from enten.worker import main

    asyncio.run(main(poll_interval=poll_interval))


@app.command(name="eval")
def eval_command(
    corpus: str = typer.Option(..., "--corpus", help="Directory containing eval fixtures."),
    report: str = typer.Option(..., "--report", help="Path to write the JSON eval report."),
    stub_llm: bool = typer.Option(False, "--stub-llm", help="Use a deterministic stub LLM."),
    baseline_update: bool = typer.Option(
        False,
        "--baseline-update",
        help="Write the report to .omo/baselines/eval-baseline.json.",
    ),
    threshold: float = typer.Option(
        0.0,
        "--threshold",
        help="Minimum aggregate precision required (placeholder for CI gating).",
    ),
) -> None:
    """Run the eval harness against a fixture corpus."""
    from enten.eval import EvalError, run_eval, write_baseline

    async def _run() -> EvalReport:
        return await run_eval(corpus, stub_llm=stub_llm)

    try:
        result = asyncio.run(_run())
    except EvalError as exc:
        typer.echo(f"Eval error: {exc}", err=True)
        raise typer.Exit(1) from exc

    report_path = Path(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(result.model_dump(), indent=2, sort_keys=True)
    report_path.write_text(report_json, encoding="utf-8")
    typer.echo(f"Wrote eval report to {report_path}")

    if baseline_update:
        baseline_path = write_baseline(result)
        typer.echo(f"Updated baseline at {baseline_path}")

    if threshold > 0:
        aggregate_precision = _aggregate_precision(result)
        if aggregate_precision < threshold:
            typer.echo(
                f"Precision {aggregate_precision:.4f} below threshold {threshold:.4f}",
                err=True,
            )
            raise typer.Exit(2)


def _aggregate_precision(report: EvalReport) -> float:
    """Compute fixture-weighted average precision across all kinds."""
    total_correct = sum(m.n_correct for m in report.by_kind.values())
    total_bot = total_correct + sum(m.n_hallucinated for m in report.by_kind.values())
    return total_correct / total_bot if total_bot else 0.0


if __name__ == "__main__":
    app()
