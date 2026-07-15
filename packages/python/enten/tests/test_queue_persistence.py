from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
import pytest_asyncio

from enten.migrations import apply_migrations
from enten.queue import (
    EffectState,
    claim_next,
    complete_effect,
    enqueue,
    get_effect,
    init_queue,
    list_latest_review_checkpoints,
    load_latest_review_checkpoint,
    maintain_lease,
    mark_state,
    prepare_effect,
    record_effect_attempt,
    renew_lease,
    save_review_checkpoint,
)
from enten.queue.persistence import JsonValue
from enten.worker import clean_stale_jobs

MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"


@pytest_asyncio.fixture
async def durable_queue(db_session: Any) -> Any:
    pool = db_session["pool"]
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await conn.execute("DROP SCHEMA IF EXISTS vec CASCADE")
        await apply_migrations(conn, MIGRATIONS)
    init_queue(pool)
    return pool


@pytest.mark.slow
async def test_concurrent_claims_fence_a_job_to_one_worker(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {"delivery_id": "d-1"}})
    claimed: list[Any] = []

    async def claim(worker_id: str) -> None:
        claimed.append(await claim_next(worker_id))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim, "worker-a")
        tasks.start_soon(claim, "worker-b")
    jobs = [job for job in claimed if job is not None]

    assert len(jobs) == 1
    assert jobs[0]["run_id"] == run_id
    assert jobs[0]["claimed_by"] in {"worker-a", "worker-b"}
    assert jobs[0]["generation"] == 1
    assert jobs[0]["lease_expires_at"] is not None


@pytest.mark.slow
async def test_claim_returns_persisted_trace_identity(durable_queue: Any) -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    run_id = await enqueue({"kind": "pr_review", "payload": {}, "trace_id": trace_id})

    claimed = await claim_next("worker-trace")

    assert claimed is not None
    assert claimed["run_id"] == run_id
    assert claimed["trace_id"] == trace_id


@pytest.mark.slow
async def test_expired_lease_reclaim_invalidates_stale_generation(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    first = await claim_next("worker-a", lease_duration=timedelta(seconds=30))
    assert first is not None
    async with durable_queue.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE run_id = $1",
            run_id,
        )

    assert await claim_next("worker-b") is None
    assert await clean_stale_jobs() == 1
    second = await claim_next("worker-b")
    assert second is not None
    assert second["generation"] == first["generation"] + 1
    assert not await mark_state(run_id, "reviewing", generation=first["generation"])
    assert await mark_state(run_id, "reviewing", generation=second["generation"])


@pytest.mark.slow
async def test_renewed_lease_prevents_clock_advanced_stale_reclaim(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    first = await claim_next("worker-a", lease_duration=timedelta(seconds=1))
    assert first is not None
    async with durable_queue.acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET lease_expires_at = now() + interval '1 second',
                updated_at = now() - interval '1 hour'
            WHERE run_id = $1
            """,
            run_id,
        )

    assert await renew_lease(
        run_id,
        worker_id="worker-a",
        generation=first["generation"],
        lease_duration=timedelta(minutes=5),
    )
    second = await claim_next("worker-b")

    assert second is None


@pytest.mark.slow
async def test_renew_lease_rejects_stale_generation_and_worker(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    first = await claim_next("worker-a")
    assert first is not None

    assert not await renew_lease(
        run_id,
        worker_id="worker-b",
        generation=first["generation"],
    )
    assert not await renew_lease(
        run_id,
        worker_id="worker-a",
        generation=first["generation"] - 1,
    )


async def test_maintain_lease_renews_while_claim_scope_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewed = anyio.Event()
    calls: list[tuple[str, str, int]] = []

    async def fake_renew(
        run_id: str,
        *,
        worker_id: str,
        generation: int,
        lease_duration: timedelta,
    ) -> bool:
        calls.append((run_id, worker_id, generation))
        renewed.set()
        await anyio.sleep(0)
        return True

    monkeypatch.setattr("enten.queue.leases.renew_lease", fake_renew)

    async with maintain_lease(
        "run-slow",
        worker_id="worker-slow",
        generation=4,
        lease_duration=timedelta(seconds=2),
        heartbeat_interval=timedelta(milliseconds=1),
    ):
        await renewed.wait()

    assert calls
    assert set(calls) == {("run-slow", "worker-slow", 4)}


@pytest.mark.slow
async def test_review_checkpoint_is_guarded_by_claim_generation(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    claim = await claim_next("worker-a")
    assert claim is not None

    assert await save_review_checkpoint(
        run_id,
        "reviewing",
        claim["generation"],
        {"finding_ids": ["f-1"]},
    )
    assert not await save_review_checkpoint(
        run_id,
        "testing",
        claim["generation"] - 1,
        {"test": "stale"},
    )

    async with durable_queue.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stage, generation, checkpoint FROM review_checkpoints WHERE run_id = $1",
            run_id,
        )
    assert row["stage"] == "reviewing"
    assert row["generation"] == claim["generation"]
    assert json.loads(row["checkpoint"]) == {"finding_ids": ["f-1"]}


@pytest.mark.slow
async def test_load_latest_checkpoint_prefers_generation_then_stage(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    claim = await claim_next("worker-checkpoint")
    assert claim is not None
    generation = claim["generation"]
    assert await save_review_checkpoint(run_id, "reviewing", generation, {"step": 1})
    assert await save_review_checkpoint(run_id, "verifying", generation, {"step": 3})
    assert await save_review_checkpoint(run_id, "testing", generation, {"step": 2})
    async with durable_queue.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE run_id = $1",
            run_id,
        )
    assert await clean_stale_jobs() == 1
    reclaimed = await claim_next("worker-checkpoint-retry")
    assert reclaimed is not None
    assert await save_review_checkpoint(
        run_id,
        "reviewing",
        reclaimed["generation"],
        {"step": 4},
    )

    latest = await load_latest_review_checkpoint(run_id)

    assert latest is not None
    assert latest["stage"] == "reviewing"
    assert latest["generation"] == reclaimed["generation"]
    assert latest["checkpoint"] == {"step": 4}


@pytest.mark.slow
async def test_load_latest_checkpoint_orders_stages_within_generation(
    durable_queue: Any,
) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    claim = await claim_next("worker-stage-order")
    assert claim is not None
    generation = claim["generation"]
    assert await save_review_checkpoint(run_id, "verifying", generation, {"step": 3})
    assert await save_review_checkpoint(run_id, "reviewing", generation, {"step": 1})
    assert await save_review_checkpoint(run_id, "testing", generation, {"step": 2})

    latest = await load_latest_review_checkpoint(run_id)

    assert latest is not None
    assert latest["stage"] == "verifying"
    assert latest["checkpoint"] == {"step": 3}


@pytest.mark.slow
async def test_list_latest_checkpoints_returns_one_decoded_row_per_run(
    durable_queue: Any,
) -> None:
    run_ids: list[str] = []
    for worker_id in ("checkpoint-a", "checkpoint-b"):
        run_id = await enqueue({"kind": "pr_review", "payload": {}})
        claim = await claim_next(worker_id)
        assert claim is not None
        assert await save_review_checkpoint(
            run_id,
            "reviewing",
            claim["generation"],
            {"worker": worker_id},
        )
        run_ids.append(run_id)

    latest = await list_latest_review_checkpoints()

    selected = [row for row in latest if row["run_id"] in run_ids]
    assert [row["run_id"] for row in selected] == sorted(run_ids)
    workers: set[str] = set()
    for row in selected:
        checkpoint = row["checkpoint"]
        assert isinstance(checkpoint, dict)
        worker = checkpoint.get("worker")
        assert isinstance(worker, str)
        workers.add(worker)
    assert workers == {"checkpoint-a", "checkpoint-b"}


@pytest.mark.slow
async def test_ambiguous_effect_retry_remains_indeterminate(durable_queue: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    claim = await claim_next("worker-a")
    assert claim is not None
    generation = claim["generation"]

    receipt = await prepare_effect(
        run_id,
        "github-review",
        generation,
        marker="enten:delivery-1:review",
        request={"body_hash": "abc"},
    )
    assert receipt["state"] is EffectState.PREPARED
    assert await record_effect_attempt(run_id, "github-review", generation)

    duplicate = await prepare_effect(
        run_id,
        "github-review",
        generation,
        marker="enten:delivery-1:review",
        request={"body_hash": "abc"},
    )
    assert duplicate["state"] is EffectState.INDETERMINATE
    assert not await complete_effect(
        run_id,
        "github-review",
        generation,
        external_id="review-123",
    )
    persisted = await get_effect(run_id, "github-review")
    assert persisted is not None
    assert persisted["state"] is EffectState.INDETERMINATE


@pytest.mark.slow
async def test_prepared_effect_is_refenced_to_reclaimed_generation(
    durable_queue: Any,
) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    first = await claim_next("worker-a", lease_duration=timedelta(seconds=30))
    assert first is not None
    request: JsonValue = {"body_hash": "abc"}
    prepared = await prepare_effect(
        run_id,
        "github-comment",
        first["generation"],
        marker="enten:delivery-1:comment",
        request=request,
    )
    assert prepared["state"] is EffectState.PREPARED
    async with durable_queue.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE run_id = $1",
            run_id,
        )
    assert await clean_stale_jobs() == 1
    reclaimed = await claim_next("worker-b")
    assert reclaimed is not None

    refenced = await prepare_effect(
        run_id,
        "github-comment",
        reclaimed["generation"],
        marker="enten:delivery-1:comment",
        request=request,
    )

    assert refenced["state"] is EffectState.PREPARED
    assert refenced["generation"] == reclaimed["generation"]
    assert await record_effect_attempt(run_id, "github-comment", reclaimed["generation"])
