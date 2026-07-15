"""Postgres-backed job queue API for the enten worker.

This module provides the minimal surface the worker needs:

* :func:`init_queue` — bind an asyncpg pool once per process.
* :func:`enqueue` — create a ``queued`` job, optionally idempotently.
* :func:`claim_next` — atomically claim the next queued job with
  ``SELECT FOR UPDATE SKIP LOCKED``.
* :func:`mark_state` — move a job to a new state.
* :func:`mark_failed` — record a failure, increment ``attempts``, and either
  return the job to ``queued`` or mark it ``failed`` permanently.

The API intentionally avoids SQLAlchemy so that queue helpers can be used
without pulling in the full model stack, and so tests can point the queue at
a testcontainers Postgres pool without fighting module-level settings.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from enten.logging import new_run_id
from enten.queue.pool import get_queue_pool, init_queue


class JobPayload(TypedDict, total=False):
    """Flexible payload accepted by :func:`enqueue`."""

    run_id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str | None
    trace_id: str | None


class Job(TypedDict):
    """A job row as returned by :func:`claim_next`."""

    id: str
    run_id: str
    kind: str
    state: str
    payload: dict[str, Any]
    attempts: int
    claimed_by: str
    generation: int
    lease_expires_at: datetime
    trace_id: str | None


async def enqueue(payload: JobPayload) -> str:
    """Insert a ``queued`` job and return its ``run_id``.

    If ``idempotency_key`` is provided the insert is idempotent: a duplicate
    key updates ``payload`` and ``updated_at`` but keeps the existing run id.
    """
    pool = get_queue_pool()
    run_id = payload.get("run_id") or new_run_id()
    kind = payload.get("kind", "pr_review")
    data = json.dumps(payload.get("payload") or {})
    idempotency_key = payload.get("idempotency_key")
    trace_id = payload.get("trace_id")

    async with pool.acquire() as conn:
        if idempotency_key:
            row = await conn.fetchrow(
                """
                INSERT INTO jobs (run_id, kind, state, payload, idempotency_key, trace_id)
                VALUES ($1, $2, 'queued', $3, $4, $5)
                ON CONFLICT (idempotency_key) DO UPDATE
                SET payload = EXCLUDED.payload,
                    trace_id = EXCLUDED.trace_id,
                    updated_at = now()
                RETURNING run_id
                """,
                run_id,
                kind,
                data,
                idempotency_key,
                trace_id,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO jobs (run_id, kind, state, payload, trace_id)
                VALUES ($1, $2, 'queued', $3, $4)
                RETURNING run_id
                """,
                run_id,
                kind,
                data,
                trace_id,
            )

    assert row is not None
    return row["run_id"]


async def claim_next(
    worker_id: str,
    *,
    lease_duration: timedelta = timedelta(minutes=5),
) -> Job | None:
    """Atomically claim the oldest ``queued`` job.

    The selected row is locked with ``FOR UPDATE SKIP LOCKED`` so concurrent
    workers never claim the same job. The returned job already has
    ``state='indexing'``.
    """
    pool = get_queue_pool()
    lease_expires_at = datetime.now(UTC) + lease_duration
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH candidate AS (
                SELECT id
                FROM jobs
                WHERE state = 'queued'
                   OR (
                       state NOT IN ('done', 'failed')
                       AND lease_expires_at < now()
                   )
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE jobs AS job
            SET state = CASE WHEN job.state = 'queued' THEN 'indexing' ELSE job.state END,
                claimed_by = $1,
                claim_generation = job.claim_generation + 1,
                lease_expires_at = $2,
                updated_at = now()
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.id, job.run_id, job.kind, job.state, job.payload, job.attempts,
                      job.claimed_by, job.claim_generation, job.lease_expires_at,
                      job.trace_id
            """,
            worker_id,
            lease_expires_at,
        )
    if row is None:
        return None

    raw_payload = row["payload"]
    payload_value = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload or {}
    return {
        "id": str(row["id"]),
        "run_id": row["run_id"],
        "kind": row["kind"],
        "state": row["state"],
        "payload": payload_value,
        "attempts": row["attempts"],
        "claimed_by": row["claimed_by"],
        "generation": row["claim_generation"],
        "lease_expires_at": row["lease_expires_at"],
        "trace_id": row["trace_id"],
    }


async def mark_state(
    run_id: str,
    state: str,
    *,
    generation: int | None = None,
    **_extras: Any,
) -> bool:
    """Compare-and-swap a job state using its lease generation."""
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE jobs
            SET state = $1,
                updated_at = now(),
                claimed_by = CASE
                    WHEN $1 IN ('done', 'failed', 'queued') THEN NULL
                    ELSE claimed_by
                END,
                lease_expires_at = CASE
                    WHEN $1 IN ('done', 'failed', 'queued') THEN NULL
                    ELSE lease_expires_at
                END
            WHERE run_id = $2
              AND (
                  ($3::bigint IS NOT NULL AND claim_generation = $3 AND lease_expires_at > now())
                  OR ($3::bigint IS NULL AND claimed_by IS NULL)
              )
            """,
            state,
            run_id,
            generation,
        )
    return result == "UPDATE 1"


async def mark_failed(
    run_id: str,
    error: str | Exception,
    *,
    retry: bool = True,
    generation: int | None = None,
) -> bool:
    """Record a failure and decide whether to requeue or permanently fail.

    ``attempts`` is incremented first. If ``retry`` is true and the new
    attempt count is still below 3, the job returns to ``queued``. Otherwise
    it is marked ``failed``.
    """
    pool = get_queue_pool()
    error_text = str(error)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE jobs
            SET attempts = attempts + 1,
                last_error = $2,
                updated_at = now(),
                state = CASE
                    WHEN $3 AND attempts + 1 < 3 THEN 'queued'
                    ELSE 'failed'
                END,
                claimed_by = NULL,
                lease_expires_at = NULL
            WHERE run_id = $1
              AND (
                  ($4::bigint IS NOT NULL AND claim_generation = $4 AND lease_expires_at > now())
                  OR ($4::bigint IS NULL AND claimed_by IS NULL)
              )
            """,
            run_id,
            error_text,
            retry,
            generation,
        )
    return result == "UPDATE 1"


from enten.queue.checkpoints import (  # noqa: E402
    ReviewCheckpoint,
    ReviewStage,
    list_latest_review_checkpoints,
    load_latest_review_checkpoint,
)
from enten.queue.persistence import (  # noqa: E402
    EffectState,
    complete_effect,
    get_effect,
    mark_effect_indeterminate,
    prepare_effect,
    record_effect_attempt,
    save_review_checkpoint,
)

__all__ = [
    "EffectState",
    "ReviewCheckpoint",
    "ReviewStage",
    "claim_next",
    "complete_effect",
    "enqueue",
    "get_effect",
    "init_queue",
    "list_latest_review_checkpoints",
    "load_latest_review_checkpoint",
    "mark_effect_indeterminate",
    "mark_failed",
    "mark_state",
    "prepare_effect",
    "record_effect_attempt",
    "save_review_checkpoint",
]
