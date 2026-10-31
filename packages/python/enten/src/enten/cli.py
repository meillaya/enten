"""The enten command line.

The default command reviews the current checkout. Everything else is an
operator or development command and is deliberately hidden from the top-level
help.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import anyio
import typer

from enten.local_identity import local_content_sha as _local_content_sha
from enten.review.detect import VerificationDetectionError, detect_verify_command

if TYPE_CHECKING:
    from enten.eval import EvalReport

app = typer.Typer(
    name="enten",
    help="Review a local checkout with one command.",
    epilog=(
        "Examples:\n"
        "  enten\n"
        "  enten --verify 'python -m pytest -q'\n"
        "  enten --json"
    ),
    no_args_is_help=False,
    add_completion=False,
)

DEFAULT_REVIEW_STORE = Path(".enten/reviews.json")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    verify: Annotated[
        str,
        typer.Option(
            "--verify",
            help="Verification argv, or auto to detect a test command.",
        ),
    ] = "python -m compileall -q .",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Review the current checkout."""
    if ctx.invoked_subcommand is not None:
        return
    _review(
        path=Path.cwd(),
        verify=verify,
        json_output=json_output,
        pr_number=0,
        sha=None,
        store_path=DEFAULT_REVIEW_STORE,
        diff_file=None,
        probes="off",
    )


@app.command(hidden=True)
def worker(poll_interval: int = 5) -> None:
    """Run the enten worker process."""
    from enten.worker import main

    anyio.run(partial(main, poll_interval=poll_interval))


@app.command(name="reconcile", hidden=True)
def reconcile_command(
    run_id: Annotated[str, typer.Option("--run-id", help="Failed queue run to repair.")],
    check_id: Annotated[
        int | None,
        typer.Option("--check-id", help="Observed GitHub check run ID."),
    ] = None,
    comment_id: Annotated[
        int | None,
        typer.Option("--comment-id", help="Observed GitHub issue comment ID."),
    ] = None,
) -> None:
    """Adopt observed GitHub effects and requeue the review."""
    from enten.reconcile import ReconcileError, reconcile

    try:
        result = anyio.run(
            partial(
                reconcile,
                run_id,
                check_id=check_id,
                comment_id=comment_id,
            )
        )
    except ReconcileError as exc:
        typer.echo(f"reconcile: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, sort_keys=True))


@app.command(name="review", hidden=True)
def review_command(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Checkout to review. Defaults to the current directory.",
        ),
    ] = Path("."),
    verify: Annotated[
        str,
        typer.Option("--verify", help="Verification argv, or auto to detect."),
    ] = "python -m compileall -q .",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    pr_number: Annotated[
        int,
        typer.Option("--pr", min=0, hidden=True),
    ] = 0,
    sha: Annotated[str | None, typer.Option("--sha", hidden=True)] = None,
    store_path: Annotated[
        Path,
        typer.Option("--store-path", hidden=True),
    ] = DEFAULT_REVIEW_STORE,
    diff_file: Annotated[
        Path | None,
        typer.Option("--diff-file", exists=True, dir_okay=False, hidden=True),
    ] = None,
    probes: Annotated[
        str,
        typer.Option("--probes", hidden=True),
    ] = "off",
) -> None:
    """Review a checkout. The root command is the simple form."""
    _review(
        path=path,
        verify=verify,
        json_output=json_output,
        pr_number=pr_number,
        sha=sha,
        store_path=store_path,
        diff_file=diff_file,
        probes=probes,
    )


def _review(
    *,
    path: Path,
    verify: str,
    json_output: bool,
    pr_number: int,
    sha: str | None,
    store_path: Path,
    diff_file: Path | None,
    probes: str,
) -> None:
    from enten.pipeline import JsonReviewStore, PipelineDependencies, execute_review
    from enten.reporting.effects import NoopEffects
    from enten.review.local import LocalFindingsGenerator, LocalVerifier
    from enten.review.python_probes import LocalProbeRunner, parse_probe_command
    from enten.review.run import ReviewRequest

    if probes not in {"off", "shadow", "on"}:
        raise typer.BadParameter("probes must be off, shadow, or on", param_hint="--probes")
    command = _resolve_verify(path, verify)
    resolved_sha = sha or _local_content_sha(path, store_path)
    diff = diff_file.read_text(encoding="utf-8") if diff_file is not None else ""
    request = ReviewRequest(
        repository_id=str(path),
        repository_full_name=None,
        pr_number=pr_number,
        head_sha=resolved_sha,
        repository_path=path,
        public_repository=True,
        local_repository=True,
        verification_command=command,
        diff=diff,
    )
    dependencies = PipelineDependencies(
        store=JsonReviewStore(store_path),
        generator=LocalFindingsGenerator(),
        verifier=LocalVerifier(),
        effects=NoopEffects(),
    )
    if probes != "off":
        from enten._worker_runtime import WorkerConfigurationError, build_hypothesis_generator
        from enten.config import Settings

        settings = Settings()
        try:
            generator = build_hypothesis_generator(settings)
        except WorkerConfigurationError as exc:
            typer.echo(f"probes: {exc}", err=True)
            raise typer.Exit(2) from exc
        dependencies = replace(
            dependencies,
            hypothesis_generator=generator,
            probe_runner=LocalProbeRunner(
                parse_probe_command(settings.probe_command),
                settings.probe_timeout_seconds,
            ),
            probe_publish=probes == "on",
        )
    result = anyio.run(execute_review, request, dependencies)
    verdict = result.run.verification.verdict if result.run.verification else "error"
    findings = result.run.findings.findings if result.run.findings is not None else ()
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "review_id": result.run.review_id,
                    "stage": result.run.stage,
                    "verdict": verdict,
                    "replayed": result.replayed,
                    "findings": len(findings),
                },
                sort_keys=True,
            )
        )
    else:
        label = {"pass": "PASS", "flaky": "FLAKY", "fail": "FAIL", "error": "ERROR"}[verdict]
        cached = "  cached" if result.replayed else ""
        typer.echo(f"{label}  {shlex.join(command)}{cached}")
        confirmed = [finding for finding in findings if finding.evidence is not None]
        if confirmed:
            typer.echo(f"{len(confirmed)} confirmed finding(s)")
        typer.echo(f"review {result.run.review_id}  {store_path}")
    if verdict in {"fail", "error"}:
        raise typer.Exit(1)


def _resolve_verify(path: Path, verify: str) -> tuple[str, ...]:
    raw = verify.strip()
    if raw.lower() == "auto":
        try:
            return detect_verify_command(path)
        except VerificationDetectionError as exc:
            raise typer.BadParameter(str(exc), param_hint="--verify") from exc
    command = tuple(shlex.split(raw))
    if not command:
        raise typer.BadParameter("verification command cannot be empty", param_hint="--verify")
    return command


@app.command(name="cost", hidden=True)
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


@app.command(name="eval", hidden=True)
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
        result = anyio.run(_run)
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
