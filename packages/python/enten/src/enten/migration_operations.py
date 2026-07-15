from __future__ import annotations

from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import asyncpg

from enten.migration_core import (
    LEDGER_SQL,
    LOCK_KEY,
    BaselineMismatchError,
    Migration,
    MigrationDiscoveryError,
    UnsafeDowngradeError,
    discover_migrations,
    lock_and_create_ledger,
    validate_ledger,
)


async def apply_migrations(conn: asyncpg.Connection, path: Path) -> tuple[Migration, ...]:
    migrations = discover_migrations(path)
    applied_now: list[Migration] = []
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        ledger_exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
        managed_exists = await conn.fetchval("SELECT to_regclass('public.jobs')")
        if ledger_exists is None and managed_exists is not None:
            raise BaselineMismatchError(
                "unmanaged schema exists; verify and stamp it with baseline_existing first"
            )
        await conn.execute(LEDGER_SQL)
        applied = await validate_ledger(conn, migrations)
        for migration in migrations:
            if migration.version in applied:
                continue
            if applied and migration.version != max(applied) + 1:
                raise MigrationDiscoveryError("pending migrations are not a contiguous batch")
            await conn.execute(migration.sql)
            await conn.execute(
                """
                INSERT INTO schema_migrations (version, filename, checksum)
                VALUES ($1, $2, $3)
                """,
                migration.version,
                migration.filename,
                migration.checksum,
            )
            applied.add(migration.version)
            applied_now.append(migration)
    return tuple(applied_now)


def asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def assert_local_downgrade(database_url: str, *, authorized: bool) -> None:
    host = urlparse(asyncpg_url(database_url)).hostname
    if not authorized or host not in {"localhost", "127.0.0.1", "::1"}:
        raise UnsafeDowngradeError(
            "downgrade requires ALLOW_LOCAL_DB_DOWNGRADE=1 and a loopback database host"
        )


DOWN_SQL: Final[dict[int, str]] = {
    3: """
        DROP TABLE external_effects;
        DROP FUNCTION guard_external_effect_transition();
        DROP TABLE review_checkpoints;
        ALTER TABLE jobs DROP COLUMN lease_expires_at, DROP COLUMN claim_generation,
            DROP COLUMN claimed_by;
    """,
    2: "DROP SCHEMA vec CASCADE;",
    1: """
        DROP TABLE sandbox_runs, fixtures, llm_calls, pr_reviews, jobs CASCADE;
    """,
}


async def downgrade_migrations(
    conn: asyncpg.Connection,
    path: Path,
    *,
    target_version: int,
) -> tuple[int, ...]:
    migrations = discover_migrations(path)
    removed: list[int] = []
    async with conn.transaction():
        await lock_and_create_ledger(conn)
        applied = await validate_ledger(conn, migrations)
        if target_version < 0 or (applied and target_version >= max(applied)):
            raise UnsafeDowngradeError(f"target must be below current version: {target_version}")
        for version in sorted(applied, reverse=True):
            if version <= target_version:
                continue
            await conn.execute(DOWN_SQL[version])
            await conn.execute("DELETE FROM schema_migrations WHERE version = $1", version)
            removed.append(version)
    return tuple(removed)
