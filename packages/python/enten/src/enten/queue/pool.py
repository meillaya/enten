from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None


def init_queue(pool: asyncpg.Pool) -> None:
    global _pool
    _pool = pool


def get_queue_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Queue not initialized; call init_queue(pool) first")
    return _pool
