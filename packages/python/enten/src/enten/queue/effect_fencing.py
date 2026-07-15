from __future__ import annotations

from typing import Protocol

import asyncpg


class EffectFencingConnection(Protocol):
    async def fetchrow(
        self,
        query: str,
        *args: str | int,
    ) -> asyncpg.Record | None: ...


async def refence_prepared_effect(
    conn: EffectFencingConnection,
    run_id: str,
    effect_key: str,
    generation: int,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        UPDATE external_effects AS effect
        SET generation = $3
        FROM jobs
        WHERE effect.run_id = jobs.run_id
          AND effect.run_id = $1
          AND effect.effect_key = $2
          AND effect.state = 'prepared'
          AND effect.generation < $3
          AND jobs.claim_generation = $3
          AND jobs.lease_expires_at > now()
        RETURNING effect.run_id, effect.effect_key, effect.generation,
                  effect.state, effect.marker, effect.request,
                  effect.external_id, effect.last_error
        """,
        run_id,
        effect_key,
        generation,
    )
