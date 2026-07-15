from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from enten.queue import ReviewStage
from enten.review.postgres_store import PostgresReviewStore, StaleReviewCheckpointError
from enten.review.run import ReviewRequest, ReviewRun


def _run(tmp_path: Path, stage: str = "created") -> ReviewRun:
    return ReviewRun(
        review_id="review-1",
        request=ReviewRequest(
            repository_id="101",
            repository_full_name="public/repo",
            pr_number=42,
            head_sha="a" * 40,
            repository_path=tmp_path,
            public_repository=True,
            local_repository=False,
            verification_command=("true",),
            correlation_run_id="queue-1",
        ),
        stage=stage,
    )


async def test_postgres_store_saves_full_run_with_stage_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[tuple[str, str, int, object]] = []

    async def save(run_id: str, stage: str, generation: int, checkpoint: object) -> bool:
        captured.append((run_id, stage, generation, checkpoint))
        return True

    monkeypatch.setattr("enten.review.postgres_store.save_review_checkpoint", save)
    store = PostgresReviewStore(run_id="queue-1", generation=7)

    await store.save(_run(tmp_path, stage="completed"))

    assert captured[0][:3] == ("queue-1", ReviewStage.COMMENTING, 7)
    assert isinstance(captured[0][3], dict)
    assert captured[0][3]["stage"] == "completed"


async def test_postgres_store_rehydrates_latest_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, stage="verified")

    async def load(_run_id: str):
        return {
            "run_id": "queue-1",
            "stage": ReviewStage.VERIFYING,
            "generation": 7,
            "checkpoint": run.model_dump(mode="json"),
            "updated_at": datetime.now(UTC),
        }

    monkeypatch.setattr("enten.review.postgres_store.load_latest_review_checkpoint", load)
    store = PostgresReviewStore(run_id="queue-1", generation=7)

    loaded = await store.get_by_key(run.request.key)

    assert loaded == run


async def test_postgres_store_rejects_stale_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def stale(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr("enten.review.postgres_store.save_review_checkpoint", stale)
    store = PostgresReviewStore(run_id="queue-1", generation=6)

    with pytest.raises(StaleReviewCheckpointError):
        await store.save(_run(tmp_path))
