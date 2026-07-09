"""enten worker process.

The worker polls the Postgres job queue, claims one job at a time, runs it
through the PR review pipeline, and handles graceful shutdown, retries, and
stale-job recovery.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from typing import Any

import asyncpg

from enten.config import Settings
from enten.logging import bind_context, clear_context, configure_logging, get_logger
from enten.queue import (
    _get_pool,
    claim_next,
    init_queue,
    mark_failed,
    mark_state,
)

try:
    from enten.otel import configure_otel as _configure_otel
    from enten.otel import tracer as _tracer
except Exception:  # pragma: no cover - T05 may not be present yet
    _configure_otel = None

    class _NoopTracer:
        class _Span:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def set_attribute(self, *_args: object) -> None:
                return None

        def start_as_current_span(self, _name: str) -> _NoopTracer._Span:
            return self._Span()

    _tracer = _NoopTracer


PIPELINE_STATES = ["indexing", "reviewing", "testing", "verifying", "commenting", "done"]


async def run_pipeline(run_id: str) -> None:
    """Stub pipeline that exercises the job state machine.

    The real indexing/review/verify/post stages will replace this stub in
    later tasks. For now we simply transition through every state with a
    short sleep so tests can observe the progression.
    """
    logger = get_logger(__name__)
    # ``claim_next`` already moved the job to ``indexing``.
    for state in PIPELINE_STATES[1:]:
        await mark_state(run_id, state)
        logger.info("pipeline_state_transition", run_id=run_id, state=state)
        await asyncio.sleep(0.1)


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
        SET state = CASE
                WHEN attempts < 3 THEN 'queued'
                ELSE 'failed'
            END,
            last_error = CASE
                WHEN attempts < 3 THEN 'stale job reset'
                ELSE 'stale job expired'
            END,
            updated_at = now()
        WHERE state = ANY($1::text[])
          AND updated_at < now() - ($2 * interval '1 minute')
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
    queue pool. The loop then claims jobs, runs the pipeline stub, and
    handles failures with bounded retries.
    """
    configure_logging()
    if _configure_otel is not None:
        _configure_otel()

    settings = Settings()
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=4,
    )
    init_queue(pool)

    logger = get_logger(__name__)
    worker_id = f"worker-{os.getpid()}"
    logger.info("worker_starting", poll_interval=poll_interval, worker_id=worker_id)

    await clean_stale_jobs()
    last_clean = time.monotonic()

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(sig_name: str) -> None:
        logger.info("shutdown_signal_received", signal=sig_name, worker_id=worker_id)
        shutdown_event.set()

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _on_signal, sig.name)
    except (ValueError, NotImplementedError):
        # Signals are unavailable on Windows or in some test runners.
        pass

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
                with _tracer().start_as_current_span("pr_review") as span:
                    span.set_attribute("run_id", run_id)
                    await run_pipeline(run_id)
                logger.info("job_completed", run_id=run_id, worker_id=worker_id)
            except Exception as exc:
                logger.exception(
                    "job_failed",
                    run_id=run_id,
                    error=str(exc),
                    worker_id=worker_id,
                )
                if job["attempts"] < 3:
                    await mark_failed(run_id, str(exc), retry=True)
                else:
                    await mark_state(run_id, "failed")
            finally:
                clear_context()

            # Avoid a tight loop after finishing a job.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=1)
    finally:
        await pool.close()
        logger.info("worker_stopped", worker_id=worker_id)
