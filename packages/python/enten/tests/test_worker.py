"""End-to-end tests for the enten worker process.

These tests spin up a real Postgres container via testcontainers, apply the
T08 migration, and exercise the worker's claim/run/retry/recovery paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from enten import worker as worker_mod
from enten.queue import claim_next, enqueue, init_queue, mark_state
from enten.worker import clean_stale_jobs, run_pipeline

MIGRATION_PATH = Path(__file__).parents[4] / "migrations" / "0001_init.sql"


async def _reset_schema(pool: Any) -> None:
    """Drop all application tables so each test starts from a clean slate."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DROP TABLE IF EXISTS sandbox_runs, fixtures, llm_calls,
                                 pr_reviews, jobs CASCADE
            """
        )


async def _apply_migration(pool: Any) -> None:
    """Run the T08 migration SQL against a fresh testcontainers pool."""
    sql = MIGRATION_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)


@pytest_asyncio.fixture
async def migrated_db(db_session: Any) -> Any:
    """Provide a test DB pool with the full schema and queue initialized."""
    await _reset_schema(db_session["pool"])
    await _apply_migration(db_session["pool"])
    init_queue(db_session["pool"])
    yield db_session


async def test_worker_picks_up_job_and_transitions_to_done(migrated_db: Any) -> None:
    """A queued job is claimed and transitions through the stub pipeline to done."""
    run_id = await enqueue({"kind": "pr_review", "payload": {"pr_number": 1}})

    job = await claim_next("worker-test")
    assert job is not None
    assert job["run_id"] == run_id
    assert job["state"] == "indexing"

    await run_pipeline(run_id)

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "done"
    assert row["attempts"] == 0


async def test_worker_retries_on_exception(
    migrated_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing pipeline is requeued with an incremented attempt count."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})

    async def _boom(_rid: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "run_pipeline", _boom)

    job = await claim_next("worker-test")
    assert job is not None

    try:
        await worker_mod.run_pipeline(run_id)
    except Exception as exc:
        await worker_mod.mark_failed(run_id, exc, retry=True)

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "queued"
    assert row["attempts"] == 1


async def test_worker_fails_permanently_after_three_attempts(
    migrated_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After three failed attempts the job stays failed permanently."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})

    async def _boom(_rid: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "run_pipeline", _boom)

    for _ in range(3):
        job = await claim_next("worker-test")
        assert job is not None
        assert job["run_id"] == run_id

        try:
            await worker_mod.run_pipeline(run_id)
        except Exception as exc:
            if job["attempts"] < 3:
                await worker_mod.mark_failed(run_id, exc, retry=True)
            else:
                await worker_mod.mark_state(run_id, "failed")

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "failed"
    assert row["attempts"] == 3


async def test_clean_stale_jobs_resets_stale_indexing_job(migrated_db: Any) -> None:
    """A job stuck in ``indexing`` longer than the threshold is requeued."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    await mark_state(run_id, "indexing")

    async with migrated_db["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET updated_at = now() - interval '30 minutes' WHERE run_id = $1",
            run_id,
        )

    count = await clean_stale_jobs(threshold_minutes=15)
    assert count == 1

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, last_error FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "queued"
    assert "stale" in row["last_error"]
