"""Verify the raw-SQL migration creates the expected tables."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = "postgresql+asyncpg://enten:enten@localhost:5433/enten"
EXPECTED_TABLES = {"jobs", "pr_reviews", "llm_calls", "fixtures", "sandbox_runs", "chunks"}


@pytest.mark.slow
async def test_db_migration_creates_tables() -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "db-migrate.sh")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    assert set(tables) >= EXPECTED_TABLES
