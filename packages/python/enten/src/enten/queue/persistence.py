"""Fenced review checkpoints and external-effect receipts."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TypedDict

from enten.queue.effect_fencing import refence_prepared_effect
from enten.queue.pool import get_queue_pool

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class EffectState(StrEnum):
    PREPARED = "prepared"
    ATTEMPTED = "attempted"
    SUCCEEDED = "succeeded"
    INDETERMINATE = "indeterminate"


class EffectReceipt(TypedDict):
    run_id: str
    effect_key: str
    generation: int
    state: EffectState
    marker: str
    request: JsonValue
    external_id: str | None
    last_error: str | None


class EffectConflictError(RuntimeError):
    """An effect key or marker was reused for different request data."""


def _receipt(row) -> EffectReceipt:
    request = json.loads(row["request"]) if isinstance(row["request"], str) else row["request"]
    return {
        "run_id": row["run_id"],
        "effect_key": row["effect_key"],
        "generation": row["generation"],
        "state": EffectState(row["state"]),
        "marker": row["marker"],
        "request": request,
        "external_id": row["external_id"],
        "last_error": row["last_error"],
    }


async def save_review_checkpoint(
    run_id: str,
    stage: str,
    generation: int,
    checkpoint: JsonValue,
) -> bool:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO review_checkpoints (run_id, stage, generation, checkpoint)
            SELECT run_id, $2, $3, $4::jsonb
            FROM jobs
            WHERE run_id = $1
              AND claim_generation = $3
              AND lease_expires_at > now()
            ON CONFLICT (run_id, stage) DO UPDATE
            SET generation = EXCLUDED.generation,
                checkpoint = EXCLUDED.checkpoint,
                updated_at = now()
            WHERE review_checkpoints.generation <= EXCLUDED.generation
            """,
            run_id,
            stage,
            generation,
            json.dumps(checkpoint),
        )
    return result == "INSERT 0 1"


async def get_effect(run_id: str, effect_key: str) -> EffectReceipt | None:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, effect_key, generation, state, marker, request,
                   external_id, last_error
            FROM external_effects
            WHERE run_id = $1 AND effect_key = $2
            """,
            run_id,
            effect_key,
        )
    return None if row is None else _receipt(row)


async def prepare_effect(
    run_id: str,
    effect_key: str,
    generation: int,
    *,
    marker: str,
    request: JsonValue,
) -> EffectReceipt:
    pool = get_queue_pool()
    encoded_request = json.dumps(request, sort_keys=True, separators=(",", ":"))
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO external_effects
                (run_id, effect_key, generation, state, marker, request)
            SELECT run_id, $2, $3, 'prepared', $4, $5::jsonb
            FROM jobs
            WHERE run_id = $1
              AND claim_generation = $3
              AND lease_expires_at > now()
            ON CONFLICT DO NOTHING
            """,
            run_id,
            effect_key,
            generation,
            marker,
            encoded_request,
        )
        row = await conn.fetchrow(
            """
            SELECT run_id, effect_key, generation, state, marker, request,
                   external_id, last_error
            FROM external_effects
            WHERE run_id = $1 AND effect_key = $2
            FOR UPDATE
            """,
            run_id,
            effect_key,
        )
        if row is None:
            raise EffectConflictError("effect could not be prepared with the current lease")
        if row["marker"] != marker or _receipt(row)["request"] != json.loads(encoded_request):
            raise EffectConflictError("effect key was reused with different request data")
        if row["state"] == EffectState.PREPARED and row["generation"] != generation:
            row = await refence_prepared_effect(
                conn,
                run_id,
                effect_key,
                generation,
            )
            if row is None:
                raise EffectConflictError(
                    "prepared effect could not be fenced to the current lease"
                )
        if row["state"] == EffectState.ATTEMPTED:
            await conn.execute(
                """
                UPDATE external_effects AS effect
                SET state = 'indeterminate', generation = $3,
                    last_error = 'prior effect attempt has no durable success receipt'
                FROM jobs
                WHERE effect.run_id = jobs.run_id
                  AND effect.run_id = $1
                  AND effect.effect_key = $2
                  AND effect.state = 'attempted'
                  AND jobs.claim_generation = $3
                  AND jobs.lease_expires_at > now()
                """,
                run_id,
                effect_key,
                generation,
            )
            row = await conn.fetchrow(
                """
                SELECT run_id, effect_key, generation, state, marker, request,
                       external_id, last_error
                FROM external_effects
                WHERE run_id = $1 AND effect_key = $2
                """,
                run_id,
                effect_key,
            )
            if row is None:
                raise EffectConflictError("effect receipt disappeared during recovery")
        return _receipt(row)


async def record_effect_attempt(run_id: str, effect_key: str, generation: int) -> bool:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE external_effects AS effect
            SET state = 'attempted', attempted_at = now()
            FROM jobs
            WHERE effect.run_id = jobs.run_id
              AND effect.run_id = $1
              AND effect.effect_key = $2
              AND effect.generation = $3
              AND effect.state = 'prepared'
              AND jobs.claim_generation = $3
              AND jobs.lease_expires_at > now()
            """,
            run_id,
            effect_key,
            generation,
        )
    return result == "UPDATE 1"


async def complete_effect(
    run_id: str,
    effect_key: str,
    generation: int,
    *,
    external_id: str,
) -> bool:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE external_effects AS effect
            SET state = 'succeeded', external_id = $4, completed_at = now()
            FROM jobs
            WHERE effect.run_id = jobs.run_id
              AND effect.run_id = $1
              AND effect.effect_key = $2
              AND effect.generation = $3
              AND effect.state = 'attempted'
              AND jobs.claim_generation = $3
              AND jobs.lease_expires_at > now()
            """,
            run_id,
            effect_key,
            generation,
            external_id,
        )
    return result == "UPDATE 1"


async def mark_effect_indeterminate(
    run_id: str,
    effect_key: str,
    generation: int,
    error: str,
) -> bool:
    pool = get_queue_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE external_effects AS effect
            SET state = 'indeterminate', generation = $3, last_error = $4
            FROM jobs
            WHERE effect.run_id = jobs.run_id
              AND effect.run_id = $1
              AND effect.effect_key = $2
              AND effect.state = 'attempted'
              AND jobs.claim_generation = $3
              AND jobs.lease_expires_at > now()
            """,
            run_id,
            effect_key,
            generation,
            error,
        )
    return result == "UPDATE 1"
