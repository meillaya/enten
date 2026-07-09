from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from enten.embeddings import EmbeddingUnavailableError, OpenAICompatEmbeddings
from enten.indexer import chunker

REPO_ROOT = Path(__file__).resolve().parents[4]


@asynccontextmanager
async def _db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest.fixture
async def pg_engine():
    with PostgresContainer("pgvector/pgvector:pg16") as postgres:
        dsn = postgres.get_connection_url().replace("+psycopg2", "")
        conn = await asyncpg.connect(dsn)
        for name in ("0001_init.sql", "0002_pgvector.sql"):
            sql = (REPO_ROOT / "migrations" / name).read_text()
            await conn.execute(sql)
        await conn.close()

        engine = create_async_engine(postgres.get_connection_url().replace("+psycopg2", "+asyncpg"))
        yield engine
        await engine.dispose()


class _RecordingEmbeddings:
    def __init__(self, dimension: int = 1024) -> None:
        self.calls: list[int] = []
        self.dimension = dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [[0.0] * self.dimension for _ in texts]


@pytest.mark.slow
async def test_reindex_unchanged_repo_is_idempotent(pg_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "enten.indexer.pipeline.get_session", lambda: _db_session(pg_engine)
    )
    from enten.indexer.pipeline import index_repo_for_repo

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello():\n    pass\n")

    embed = _RecordingEmbeddings()
    stats1 = await index_repo_for_repo(str(repo), "repo1", embed)
    assert stats1.indexed > 0
    assert stats1.skipped == 0

    stats2 = await index_repo_for_repo(str(repo), "repo1", embed)
    assert stats2.indexed == 0
    assert stats2.updated == 0
    assert stats2.skipped == stats1.indexed
    assert embed.calls == [stats1.indexed]


@pytest.mark.slow
async def test_indexing_batches_chunks(pg_engine, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "enten.indexer.pipeline.get_session", lambda: _db_session(pg_engine)
    )
    from enten.indexer.pipeline import index_repo_for_repo

    def fake_chunk(repo_path):
        return [
            chunker.Chunk(
                chunk_id=f"id-{i}",
                path="a.py",
                lang="python",
                start_line=i + 1,
                end_line=i + 2,
                start_byte=0,
                end_byte=1,
                text=f"chunk {i}",
                symbols=[],
            )
            for i in range(200)
        ]

    monkeypatch.setattr(chunker, "chunk", fake_chunk)

    embed = _RecordingEmbeddings()
    repo = tmp_path / "repo"
    repo.mkdir()
    stats = await index_repo_for_repo(str(repo), "repo2", embed)

    assert stats.indexed == 200
    assert stats.updated == 0
    assert stats.skipped == 0
    assert len(embed.calls) == 4
    assert all(size <= 64 for size in embed.calls)
    assert sum(embed.calls) == 200


@respx.mock
async def test_embed_timeout_retries_and_raises():
    route = respx.post("https://embed.test/v1/embeddings").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    embed = OpenAICompatEmbeddings(
        base_url="https://embed.test/v1",
        api_key="sk-test",
        max_retries=3,
    )
    with pytest.raises(EmbeddingUnavailableError):
        await embed.embed_batch(["hello"])
    assert route.call_count == 3
