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
from typing import Any, TypedDict

import asyncpg

from enten.logging import new_run_id

_pool: asyncpg.Pool | None = None


class JobPayload(TypedDict, total=False):
    """Flexible payload accepted by :func:`enqueue`."""

    run_id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str | None


class Job(TypedDict):
    """A job row as returned by :func:`claim_next`."""

    id: str
    run_id: str
    kind: str
    state: str
    payload: dict[str, Any]
    attempts: int


def init_queue(pool: asyncpg.Pool) -> None:
    """Bind ``pool`` as the queue connection pool for this process."""
    global _pool
    _pool = pool


def _get_pool() -> asyncpg.Pool:
    """Return the bound pool or raise a clear runtime error."""
    if _pool is None:
        raise RuntimeError("Queue not initialized; call init_queue(pool) first")
    return _pool


async def enqueue(payload: JobPayload) -> str:
    """Insert a ``queued`` job and return its ``run_id``.

    If ``idempotency_key`` is provided the insert is idempotent: a duplicate
    key updates ``payload`` and ``updated_at`` but keeps the existing run id.
    """
    pool = _get_pool()
    run_id = payload.get("run_id") or new_run_id()
    kind = payload.get("kind", "pr_review")
    data = json.dumps(payload.get("payload") or {})
    idempotency_key = payload.get("idempotency_key")

    async with pool.acquire() as conn:
        if idempotency_key:
            row = await conn.fetchrow(
                """
                INSERT INTO jobs (run_id, kind, state, payload, idempotency_key)
                VALUES ($1, $2, 'queued', $3, $4)
                ON CONFLICT (idempotency_key) DO UPDATE
                SET payload = EXCLUDED.payload,
                    updated_at = now()
                RETURNING run_id
                """,
                run_id,
                kind,
                data,
                idempotency_key,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO jobs (run_id, kind, state, payload)
                VALUES ($1, $2, 'queued', $3)
                RETURNING run_id
                """,
                run_id,
                kind,
                data,
            )

    assert row is not None
    return row["run_id"]


async def claim_next(worker_id: str) -> Job | None:
    """Atomically claim the oldest ``queued`` job.

    The selected row is locked with ``FOR UPDATE SKIP LOCKED`` so concurrent
    workers never claim the same job. The returned job already has
    ``state='indexing'``.
    """
    pool = _get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
                SELECT id, run_id, kind, state, payload, attempts
                FROM jobs
                WHERE state = 'queued'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
        )
        if row is None:
            return None

        await conn.execute(
            """
                UPDATE jobs
                SET state = 'indexing',
                    updated_at = now()
                WHERE run_id = $1
                """,
            row["run_id"],
        )

    return {
        "id": str(row["id"]),
        "run_id": row["run_id"],
        "kind": row["kind"],
        "state": "indexing",
        "payload": row["payload"] or {},
        "attempts": row["attempts"],
    }


async def mark_state(run_id: str, state: str, **_extras: Any) -> None:
    """Update ``state`` for ``run_id`` and refresh ``updated_at``."""
    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET state = $1,
                updated_at = now()
            WHERE run_id = $2
            """,
            state,
            run_id,
        )


async def mark_failed(run_id: str, error: str | Exception, *, retry: bool = True) -> None:
    """Record a failure and decide whether to requeue or permanently fail.

    ``attempts`` is incremented first. If ``retry`` is true and the new
    attempt count is still below 3, the job returns to ``queued``. Otherwise
    it is marked ``failed``.
    """
    pool = _get_pool()
    error_text = str(error)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET attempts = attempts + 1,
                last_error = $2,
                updated_at = now(),
                state = CASE
                    WHEN $3 AND attempts + 1 < 3 THEN 'queued'
                    ELSE 'failed'
                END
            WHERE run_id = $1
            """,
            run_id,
            error_text,
            retry,
        )
