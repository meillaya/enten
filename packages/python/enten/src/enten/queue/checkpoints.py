from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Final, TypedDict

from enten.queue.pool import get_queue_pool

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ReviewStage(StrEnum):
    INDEXING = "indexing"
    REVIEWING = "reviewing"
    TESTING = "testing"
    VERIFYING = "verifying"
    COMMENTING = "commenting"


class ReviewCheckpoint(TypedDict):
    run_id: str
    stage: ReviewStage
    generation: int
    checkpoint: JsonValue
    updated_at: datetime


STAGE_ORDER_SQL: Final = """
CASE stage
    WHEN 'indexing' THEN 1
    WHEN 'reviewing' THEN 2
    WHEN 'testing' THEN 3
    WHEN 'verifying' THEN 4
    WHEN 'commenting' THEN 5
END
"""


def _checkpoint(row) -> ReviewCheckpoint:
    raw_checkpoint = row["checkpoint"]
    checkpoint = json.loads(raw_checkpoint) if isinstance(raw_checkpoint, str) else raw_checkpoint
    return {
        "run_id": row["run_id"],
        "stage": ReviewStage(row["stage"]),
        "generation": row["generation"],
        "checkpoint": checkpoint,
        "updated_at": row["updated_at"],
    }


async def load_latest_review_checkpoint(run_id: str) -> ReviewCheckpoint | None:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT run_id, stage, generation, checkpoint, updated_at
            FROM review_checkpoints
            WHERE run_id = $1
            ORDER BY generation DESC, {STAGE_ORDER_SQL} DESC, updated_at DESC, stage DESC
            LIMIT 1
            """,
            run_id,
        )
    return None if row is None else _checkpoint(row)


async def list_latest_review_checkpoints() -> list[ReviewCheckpoint]:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH ranked AS (
                SELECT run_id, stage, generation, checkpoint, updated_at,
                       row_number() OVER (
                           PARTITION BY run_id
                           ORDER BY generation DESC, {STAGE_ORDER_SQL} DESC,
                                    updated_at DESC, stage DESC
                       ) AS checkpoint_rank
                FROM review_checkpoints
            )
            SELECT run_id, stage, generation, checkpoint, updated_at
            FROM ranked
            WHERE checkpoint_rank = 1
            ORDER BY run_id
            """
        )
    return [_checkpoint(row) for row in rows]
