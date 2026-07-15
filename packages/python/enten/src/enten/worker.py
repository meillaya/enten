"""enten worker process.

The worker polls the Postgres job queue, claims one job at a time, runs it
through the PR review pipeline, and handles graceful shutdown, retries, and
stale-job recovery.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from opentelemetry.trace import SpanKind

from enten import _worker_runtime
from enten._worker_runtime import (
    bootstrap_worker,
    configure_worker_observability,
    install_shutdown_handlers,
    worker_tracer,
)
from enten.config import Settings
from enten.logging import bind_context, clear_context, configure_logging, get_logger
from enten.pipeline import (
    PipelineDependencies,
    ReviewJobEnvelope,
    execute_review,
)
from enten.queue import (
    claim_next,
    maintain_lease,
    mark_failed,
    mark_state,
)
from enten.queue.pool import get_queue_pool
from enten.review.remote import (
    CheckoutValidator,
    FailClosedCheckoutValidator,
    public_pull_request_checkout,
)
from enten.review.run import PipelineResult, ReviewRequest
from enten.review.tracing import JobTraceError, extract_job_context
from enten.worker_pipeline import (
    QueueStageSink as QueueStageSink,
)
from enten.worker_pipeline import (
    StaleJobLeaseError as StaleJobLeaseError,
)
from enten.worker_pipeline import (
    build_pipeline_dependencies as build_pipeline_dependencies,
)

WorkerConfigurationError = _worker_runtime.WorkerConfigurationError
build_production_github_client = _worker_runtime.build_production_github_client
parse_verification_command = _worker_runtime.parse_verification_command

PIPELINE_STATES = ["indexing", "reviewing", "testing", "verifying", "commenting", "done"]
_get_pool = get_queue_pool


async def run_pipeline(
    run_id: str,
    *,
    payload: Mapping[str, object] | None = None,
    dependencies: PipelineDependencies | None = None,
    repository_path: Path | None = None,
    checkout_validator: CheckoutValidator | None = None,
    verification_command: tuple[str, ...] | None = None,
    expected_review_contract: str | None = None,
) -> PipelineResult:
    """Parse one job and delegate all execution to the shared stage machine."""
    checkout = (repository_path or Path(".")).resolve()
    if payload is None:
        request = ReviewRequest(
            repository_id=f"worker:{run_id}",
            pr_number=0,
            head_sha=run_id,
            repository_path=checkout,
            public_repository=True,
            local_repository=True,
            verification_command=(sys.executable, "-m", "compileall", "-q", "."),
            correlation_run_id=run_id,
        )
    else:
        envelope = ReviewJobEnvelope.model_validate(payload)
        request = envelope.to_request(
            repository_path=checkout,
            correlation_run_id=run_id,
            verification_command=verification_command,
            expected_review_contract=expected_review_contract,
        )
        validator = checkout_validator or FailClosedCheckoutValidator()
        await validator.validate(request)
    resolved_dependencies = dependencies or build_pipeline_dependencies(run_id)
    return await execute_review(request, resolved_dependencies)


async def clean_stale_jobs(threshold_minutes: int = 15) -> int:
    """Reset jobs that look crashed.

    Any job stuck in an in-progress state for longer than
    ``threshold_minutes`` is either returned to ``queued`` (if it still has
    retry budget) or marked ``failed`` permanently. The function returns the
    number of jobs it touched.
    """
    logger = get_logger(__name__)
    pool = _get_pool()
    rows = await pool.fetch(
        """
        UPDATE jobs
        SET attempts = LEAST(attempts + 1, 3),
            state = CASE
                WHEN attempts + 1 < 3 THEN 'queued'
                ELSE 'failed'
            END,
            last_error = CASE
                WHEN attempts + 1 < 3 THEN 'stale job reset'
                ELSE 'stale job expired'
            END,
            claimed_by = NULL,
            lease_expires_at = NULL,
            updated_at = now()
        WHERE state = ANY($1::text[])
          AND (
              (lease_expires_at IS NOT NULL AND lease_expires_at < now())
              OR (
                  lease_expires_at IS NULL
                  AND updated_at < now() - ($2 * interval '1 minute')
              )
          )
        RETURNING run_id
        """,
        ["indexing", "reviewing", "testing", "verifying", "commenting"],
        threshold_minutes,
    )
    logger.info("clean_stale_jobs", count=len(rows), threshold_minutes=threshold_minutes)
    return len(rows)


async def main(poll_interval: int = 5) -> None:
    """Run the worker loop until SIGTERM/SIGINT.

    Initialization order matches the task spec: logging, OTel, settings,
    queue pool. The loop then claims jobs, runs the shared pipeline, and
    handles failures with bounded retries.
    """
    configure_logging()
    configure_worker_observability()

    settings = Settings()
    runtime = await bootstrap_worker(settings)
    pool = runtime.pool

    logger = get_logger(__name__)
    worker_id = f"worker-{os.getpid()}"
    logger.info("worker_starting", poll_interval=poll_interval, worker_id=worker_id)

    await clean_stale_jobs()
    last_clean = time.monotonic()

    shutdown_event = asyncio.Event()
    install_shutdown_handlers(shutdown_event, logger, worker_id)

    try:
        while not shutdown_event.is_set():
            if time.monotonic() - last_clean >= 3600:
                count = await clean_stale_jobs()
                logger.info("hourly_stale_jobs_cleaned", count=count, worker_id=worker_id)
                last_clean = time.monotonic()

            job = await claim_next(worker_id)
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=poll_interval,
                    )
                continue

            run_id = job["run_id"]
            bind_context(run_id=run_id, worker_id=worker_id)
            try:
                async with maintain_lease(
                    run_id,
                    worker_id=worker_id,
                    generation=job["generation"],
                ):
                    envelope = ReviewJobEnvelope.model_validate(job["payload"])
                    expected_trace_id = job["trace_id"]
                    if not isinstance(expected_trace_id, str):
                        raise JobTraceError("claimed job is missing its authenticated trace id")
                    parent = extract_job_context(
                        traceparent=envelope.traceparent,
                        tracestate=envelope.tracestate,
                        expected_trace_id=expected_trace_id,
                    )
                    with worker_tracer().start_as_current_span(
                        "pr_review.consume",
                        context=parent,
                        kind=SpanKind.CONSUMER,
                    ) as span:
                        span.set_attribute("run_id", run_id)
                        async with public_pull_request_checkout(
                            repository_full_name=envelope.repository.full_name,
                            pr_number=envelope.pull_request.number,
                            head_sha=envelope.pull_request.head_sha,
                        ) as repository_path:
                            await run_pipeline(
                                run_id,
                                payload=job["payload"],
                                dependencies=build_pipeline_dependencies(
                                    run_id,
                                    github_client=runtime.github_client,
                                    generation=job["generation"],
                                    verifier=runtime.remote_verifier,
                                ),
                                repository_path=repository_path,
                                checkout_validator=runtime.checkout_validator,
                                verification_command=runtime.verification_command,
                                expected_review_contract=runtime.review_contract,
                            )
                logger.info("job_completed", run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                logger.exception(
                    "job_failed",
                    run_id=run_id,
                    error=str(exc),
                    worker_id=worker_id,
                )
                if job["attempts"] < 3:
                    await mark_failed(
                        run_id,
                        str(exc),
                        retry=True,
                        generation=job["generation"],
                    )
                else:
                    await mark_state(run_id, "failed", generation=job["generation"])
            finally:
                clear_context()

            # Avoid a tight loop after finishing a job.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=1)
    finally:
        await runtime.github_client.close()
        await pool.close()
        logger.info("worker_stopped", worker_id=worker_id)
