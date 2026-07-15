"""enten CLI entry point.

Exposes local review, cost reporting, evaluation, and the production worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import anyio
import typer

if TYPE_CHECKING:
    from enten.eval import EvalReport

app = typer.Typer(
    name="enten",
    help="enten - AI engineering agent.",
    no_args_is_help=True,
    add_completion=False,
)

DEFAULT_REVIEW_STORE = Path(".enten/reviews.json")
_GENERATED_PARTS = frozenset(
    {
        ".enten",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "out",
    }
)


@app.command()
def main() -> None:
    """Print the canonical greeting."""
    typer.echo("enten")


@app.command()
def worker(poll_interval: int = 5) -> None:
    """Run the enten worker process."""
    from enten.worker import main

    asyncio.run(main(poll_interval=poll_interval))


@app.command(name="review")
def review_command(
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Local public repository checkout to review.",
        ),
    ],
    pr_number: Annotated[int, typer.Option("--pr", min=0, help="Pull request number.")] = 0,
    sha: Annotated[str | None, typer.Option("--sha", help="Reviewed head commit SHA.")] = None,
    verify_command: Annotated[
        str,
        typer.Option(
            "--verify-command",
            help="Argv-only verification command (no shell expansion).",
        ),
    ] = "python -m compileall -q .",
    store_path: Annotated[
        Path,
        typer.Option("--store-path", help="Canonical local ReviewRun store."),
    ] = DEFAULT_REVIEW_STORE,
) -> None:
    """Review a local checkout through the same stage machine as the worker."""
    from enten.pipeline import JsonReviewStore, PipelineDependencies, execute_review
    from enten.reporting.effects import NoopEffects
    from enten.review.local import LocalFindingsGenerator, LocalVerifier
    from enten.review.run import ReviewRequest

    command = tuple(shlex.split(verify_command))
    if not command:
        raise typer.BadParameter(
            "verification command cannot be empty",
            param_hint="--verify-command",
        )
    resolved_sha = sha or _local_content_sha(repo, store_path)
    request = ReviewRequest(
        repository_id=str(repo),
        repository_full_name=None,
        pr_number=pr_number,
        head_sha=resolved_sha,
        repository_path=repo,
        public_repository=True,
        local_repository=True,
        verification_command=command,
    )
    result = anyio.run(
        execute_review,
        request,
        PipelineDependencies(
            store=JsonReviewStore(store_path),
            generator=LocalFindingsGenerator(),
            verifier=LocalVerifier(),
            effects=NoopEffects(),
        ),
    )
    verdict = result.run.verification.verdict if result.run.verification else "error"
    typer.echo(
        json.dumps(
            {
                "review_id": result.run.review_id,
                "stage": result.run.stage,
                "verdict": verdict,
                "replayed": result.replayed,
            },
            sort_keys=True,
        )
    )
    if verdict in {"fail", "error"}:
        raise typer.Exit(1)


@app.command(name="cost")
def cost_command(
    pr_number: Annotated[
        int | None,
        typer.Option("--pr", min=0, help="Filter by PR number."),
    ] = None,
    repository_id: Annotated[
        str | None,
        typer.Option("--repo", help="Filter by canonical repository ID."),
    ] = None,
    store_path: Annotated[
        Path,
        typer.Option("--store-path", help="Canonical local ReviewRun store."),
    ] = DEFAULT_REVIEW_STORE,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Report tokens and USD recorded in canonical ReviewRun artifacts."""
    from enten.pipeline import JsonReviewStore, summarize_cost

    async def _summarize() -> dict[str, int | str]:
        return await summarize_cost(
            JsonReviewStore(store_path),
            pr_number=pr_number,
            repository_id=repository_id,
        )

    totals = anyio.run(_summarize)
    if json_output:
        typer.echo(json.dumps(totals, sort_keys=True))
        return
    typer.echo(
        (
            "reviews={reviews} tokens_in={tokens_in} tokens_out={tokens_out} cost_usd={cost_usd}"
        ).format(**totals)
    )


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
        help="Minimum aggregate precision required.",
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


def _local_content_sha(repository: Path, store_path: Path | None = None) -> str:
    digest = hashlib.sha256()
    excluded_store = _store_relative_to_repository(repository, store_path)
    for relative in _local_content_paths(repository):
        if _GENERATED_PARTS.intersection(relative.parts) or relative == excluded_store:
            continue
        source = repository / relative
        if source.is_symlink() or not source.is_file():
            continue
        digest.update(relative.as_posix().encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _local_content_paths(repository: Path) -> tuple[Path, ...]:
    try:
        completed = subprocess.run(
            (
                "git",
                "-C",
                str(repository),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode == 0:
        return tuple(sorted(Path(os.fsdecode(raw)) for raw in completed.stdout.split(b"\0") if raw))
    return tuple(sorted(path.relative_to(repository) for path in repository.rglob("*")))


def _store_relative_to_repository(
    repository: Path,
    store_path: Path | None,
) -> Path | None:
    if store_path is None:
        return None
    try:
        return store_path.resolve().relative_to(repository.resolve())
    except ValueError:
        return None


if __name__ == "__main__":
    app()
