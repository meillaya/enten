from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import anyio
import pytest
import pytest_asyncio

from enten.migrations import (
    KNOWN_MIGRATION_HASHES,
    BaselineMismatchError,
    ChecksumDriftError,
    MigrationDiscoveryError,
    apply_migrations,
    baseline_existing,
    discover_migrations,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = REPO_ROOT / "migrations"


@pytest_asyncio.fixture
async def clean_db(db_session: Any) -> Any:
    pool = db_session["pool"]
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await conn.execute("DROP SCHEMA IF EXISTS vec CASCADE")
    return db_session


def test_known_migration_hashes_match_raw_repository_bytes() -> None:
    for filename in ("0001_init.sql", "0002_pgvector.sql"):
        raw = (MIGRATIONS / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == KNOWN_MIGRATION_HASHES[filename]


def test_discovery_rejects_a_gap_in_versions(tmp_path: Path) -> None:
    (tmp_path / "0001_one.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0003_three.sql").write_text("SELECT 3;", encoding="utf-8")

    with pytest.raises(MigrationDiscoveryError, match="contiguous"):
        discover_migrations(tmp_path, verify_known=False)


def test_discovery_rejects_checksum_drift(tmp_path: Path) -> None:
    for filename in ("0001_init.sql", "0002_pgvector.sql"):
        (tmp_path / filename).write_bytes((MIGRATIONS / filename).read_bytes())
    (tmp_path / "0001_init.sql").write_bytes(b"-- mutated\n")

    with pytest.raises(ChecksumDriftError, match="0001_init.sql"):
        discover_migrations(tmp_path)


@pytest.mark.slow
async def test_apply_is_atomic_and_concurrent_callers_share_one_ledger(clean_db: Any) -> None:
    pool = clean_db["pool"]
    results: list[tuple[Any, ...]] = []

    async def apply_on_connection(conn: Any) -> None:
        results.append(await apply_migrations(conn, MIGRATIONS))

    async with (
        pool.acquire() as first,
        pool.acquire() as second,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(apply_on_connection, first)
        tasks.start_soon(apply_on_connection, second)

    assert sorted(len(result) for result in results) == [0, 3]
    async with pool.acquire() as conn:
        ledger = await conn.fetch(
            "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
        )
    assert [(row["version"], row["filename"]) for row in ledger] == [
        (1, "0001_init.sql"),
        (2, "0002_pgvector.sql"),
        (3, "0003_crash_consistency.sql"),
    ]


@pytest.mark.slow
async def test_apply_rejects_ledger_checksum_drift(clean_db: Any) -> None:
    pool = clean_db["pool"]
    async with pool.acquire() as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute(
            "UPDATE schema_migrations SET checksum = repeat('0', 64) WHERE version = 1"
        )
        with pytest.raises(ChecksumDriftError, match="0001_init.sql"):
            await apply_migrations(conn, MIGRATIONS)


@pytest.mark.slow
async def test_baseline_requires_exact_schema_before_stamping(clean_db: Any) -> None:
    pool = clean_db["pool"]
    async with pool.acquire() as conn:
        await conn.execute((MIGRATIONS / "0001_init.sql").read_text(encoding="utf-8"))
        await conn.execute((MIGRATIONS / "0002_pgvector.sql").read_text(encoding="utf-8"))
        await baseline_existing(conn, MIGRATIONS, through_version=2)
        versions = await conn.fetchval(
            "SELECT array_agg(version ORDER BY version) FROM schema_migrations"
        )
    assert versions == [1, 2]


@pytest.mark.slow
async def test_baseline_rejects_incomplete_schema(clean_db: Any) -> None:
    async with clean_db["pool"].acquire() as conn:
        await conn.execute("CREATE TABLE jobs (id uuid PRIMARY KEY)")
        with pytest.raises(BaselineMismatchError):
            await baseline_existing(conn, MIGRATIONS, through_version=1)


@pytest.mark.slow
async def test_baseline_rejects_schema_with_missing_constraint(clean_db: Any) -> None:
    async with clean_db["pool"].acquire() as conn:
        await conn.execute((MIGRATIONS / "0001_init.sql").read_text(encoding="utf-8"))
        await conn.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_attempts_check")
        with pytest.raises(BaselineMismatchError, match="constraints"):
            await baseline_existing(conn, MIGRATIONS, through_version=1)
