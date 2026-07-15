from __future__ import annotations

from typing import cast

from enten.queue import (
    ReviewStage as QueueReviewStage,
)
from enten.queue import (
    list_latest_review_checkpoints,
    load_latest_review_checkpoint,
    save_review_checkpoint,
)
from enten.queue.persistence import JsonValue
from enten.review.run import ReviewKey, ReviewRun, ReviewStage

_QUEUE_STAGE: dict[ReviewStage, QueueReviewStage] = {
    "created": QueueReviewStage.INDEXING,
    "proposed": QueueReviewStage.REVIEWING,
    "findings": QueueReviewStage.TESTING,
    "verified": QueueReviewStage.VERIFYING,
    "persisted": QueueReviewStage.COMMENTING,
    "reporting": QueueReviewStage.COMMENTING,
    "completed": QueueReviewStage.COMMENTING,
}


class StaleReviewCheckpointError(RuntimeError):
    """Raised when a worker no longer owns the generation it is saving."""


class PostgresReviewStore:
    """Fenced review-run storage backed by queue checkpoints."""

    def __init__(self, *, run_id: str, generation: int) -> None:
        self._run_id = run_id
        self._generation = generation

    async def save(self, run: ReviewRun) -> None:
        if run.request.correlation_run_id != self._run_id:
            raise ValueError("review correlation_run_id does not match the queue run")
        checkpoint = cast(JsonValue, run.model_dump(mode="json"))
        saved = await save_review_checkpoint(
            self._run_id,
            _QUEUE_STAGE[run.stage],
            self._generation,
            checkpoint,
        )
        if not saved:
            raise StaleReviewCheckpointError(
                f"queue generation {self._generation} no longer owns {self._run_id}"
            )

    async def get_by_key(self, key: ReviewKey) -> ReviewRun | None:
        checkpoint = await load_latest_review_checkpoint(self._run_id)
        if checkpoint is None:
            return None
        run = _parse_run(checkpoint["checkpoint"])
        return run if run.request.key == key else None

    async def list_reviews(
        self,
        *,
        pr_number: int | None = None,
        repository_id: str | None = None,
    ) -> list[ReviewRun]:
        checkpoints = await list_latest_review_checkpoints()
        reviews = [_parse_run(checkpoint["checkpoint"]) for checkpoint in checkpoints]
        return [
            run
            for run in reviews
            if (pr_number is None or run.request.pr_number == pr_number)
            and (repository_id is None or run.request.repository_id == repository_id)
        ]


def _parse_run(checkpoint: JsonValue) -> ReviewRun:
    if not isinstance(checkpoint, dict):
        raise ValueError("review checkpoint must be a JSON object")
    return ReviewRun.model_validate(checkpoint)


__all__ = ["PostgresReviewStore", "StaleReviewCheckpointError"]
