"""Verify the raw-SQL migration creates the expected tables."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = "postgresql+asyncpg://enten:enten@localhost:5433/enten"
EXPECTED_TABLES = {
    "external_effects",
    "fixtures",
    "jobs",
    "llm_calls",
    "pr_reviews",
    "review_checkpoints",
    "sandbox_runs",
    "schema_migrations",
}


@pytest.mark.slow
async def test_db_migration_creates_tables() -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "db-migrate.sh")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": DATABASE_URL},
    )
    assert result.returncode == 0, result.stderr

    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        vec_tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names(schema="vec")
        )

    assert set(tables) >= EXPECTED_TABLES
    assert "chunks" in vec_tables
