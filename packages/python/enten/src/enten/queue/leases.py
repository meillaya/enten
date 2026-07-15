from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import anyio

from enten.queue.pool import get_queue_pool

DEFAULT_LEASE_DURATION: Final = timedelta(minutes=5)
DEFAULT_HEARTBEAT_INTERVAL: Final = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class ClaimLeaseLostError(RuntimeError):
    run_id: str
    worker_id: str
    generation: int

    def __str__(self) -> str:
        return f"worker {self.worker_id!r} lost generation {self.generation} of job {self.run_id!r}"


@dataclass(frozen=True, slots=True)
class LeaseTimingError(ValueError):
    detail: str

    def __str__(self) -> str:
        return self.detail


def _require_positive(value: timedelta, name: str) -> None:
    if value <= timedelta(0):
        raise LeaseTimingError(detail=f"{name} must be greater than zero")


async def renew_lease(
    run_id: str,
    *,
    worker_id: str,
    generation: int,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> bool:
    """Extend an active generation-CAS claim using the database clock."""
    _require_positive(lease_duration, "lease_duration")
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        renewed_or_completed = await conn.fetchval(
            """
            WITH renewed AS (
                UPDATE jobs
                SET lease_expires_at = now() + $4::interval,
                    updated_at = now()
                WHERE run_id = $1
                  AND claimed_by = $2
                  AND claim_generation = $3
                  AND lease_expires_at > now()
                  AND state NOT IN ('done', 'failed', 'queued')
                RETURNING 1
            )
            SELECT EXISTS (SELECT 1 FROM renewed)
                OR EXISTS (
                    SELECT 1
                    FROM jobs
                    WHERE run_id = $1
                      AND claim_generation = $3
                      AND state IN ('done', 'failed')
                )
            """,
            run_id,
            worker_id,
            generation,
            lease_duration,
        )
    return renewed_or_completed is True


async def _heartbeat(
    run_id: str,
    worker_id: str,
    generation: int,
    lease_duration: timedelta,
    interval: timedelta,
    stopped: anyio.Event,
) -> None:
    while True:
        with anyio.move_on_after(interval.total_seconds()) as wait_scope:
            await stopped.wait()
        if not wait_scope.cancel_called:
            return
        if not await renew_lease(
            run_id,
            worker_id=worker_id,
            generation=generation,
            lease_duration=lease_duration,
        ):
            raise ClaimLeaseLostError(
                run_id=run_id,
                worker_id=worker_id,
                generation=generation,
            )


@asynccontextmanager
async def maintain_lease(
    run_id: str,
    *,
    worker_id: str,
    generation: int,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    heartbeat_interval: timedelta = DEFAULT_HEARTBEAT_INTERVAL,
) -> AsyncGenerator[None]:
    """Renew a claimed job until its checkout, pipeline, and reporting finish."""
    _require_positive(lease_duration, "lease_duration")
    _require_positive(heartbeat_interval, "heartbeat_interval")
    if heartbeat_interval >= lease_duration:
        raise LeaseTimingError(detail="heartbeat_interval must be shorter than lease_duration")
    stopped = anyio.Event()
    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(
            _heartbeat,
            run_id,
            worker_id,
            generation,
            lease_duration,
            heartbeat_interval,
            stopped,
        )
        try:
            yield
        finally:
            stopped.set()


__all__ = [
    "ClaimLeaseLostError",
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_LEASE_DURATION",
    "LeaseTimingError",
    "maintain_lease",
    "renew_lease",
]
