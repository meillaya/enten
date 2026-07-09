"""Tests for the hybrid BM25 + pgvector retrieval layer."""

from __future__ import annotations

import hashlib
from pathlib import Path

import asyncpg
import pytest
import tiktoken
from testcontainers.postgres import PostgresContainer

from enten.retrieval import EmbeddingProvider, retrieve

REPO_ROOT = Path(__file__).parents[4]
MIGRATION = REPO_ROOT / "migrations" / "0002_pgvector.sql"


class FakeEmbedding(EmbeddingProvider):
    """Deterministic embedding backend that never calls a real model."""

    DIM = 1024

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.DIM for _ in texts]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _apply_migration(dsn: str) -> None:
    raw = MIGRATION.read_text(encoding="utf-8")
    cleaned = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("--")
    )
    statements = [
        stmt.strip() for stmt in cleaned.split(";") if stmt.strip()
    ]
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


async def _seed_chunks(dsn: str) -> None:
    chunks = [
        {
            "id": f"chunk-{i:02d}",
            "path": f"src/auth/{'jwt' if i % 2 == 0 else 'oauth'}.py",
            "lang": "python",
            "start_line": i * 10 + 1,
            "end_line": i * 10 + 5,
            "text": (
                "def verify_jwt_token(token: str) -> bool:\n"
                "    \"\"\"Verify a JSON Web Token issued by the authentication service.\"\"\"\n"
                "    parts = token.split('.')\n"
                "    return len(parts) == 3 and parts[0].startswith('eyJ')"
                if i % 2 == 0
                else (
                    "def oauth_login(provider: str) -> str:\n"
                    "    \"\"\"Initiate an OAuth login flow with the selected provider.\"\"\"\n"
                    "    return f'https://{provider}/authorize'"
                )
            ),
        }
        for i in range(20)
    ]

    vector = "[" + ",".join("0.0" for _ in range(FakeEmbedding.DIM)) + "]"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE vec.chunks")
        for chunk in chunks:
            await conn.execute(
                """
                INSERT INTO vec.chunks
                (id, repo_id, path, lang, start_line, end_line, text, embedding, content_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector, $9)
                """,
                chunk["id"],
                "test-repo",
                chunk["path"],
                chunk["lang"],
                chunk["start_line"],
                chunk["end_line"],
                chunk["text"],
                vector,
                _content_hash(chunk["text"]),
            )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def postgres_container():
    """Spin up a Postgres container with the pgvector extension."""
    container = PostgresContainer("pgvector/pgvector:pg16")
    with container:
        yield container


@pytest.fixture
async def retrieval_env(monkeypatch, postgres_container):
    """Point retrieval at the test container and seed the chunks table."""
    base_url = postgres_container.get_connection_url()
    asyncpg_dsn = base_url.replace("postgresql+psycopg2", "postgresql")
    sqlalchemy_dsn = base_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

    await _apply_migration(asyncpg_dsn)
    await _seed_chunks(asyncpg_dsn)

    monkeypatch.setenv("DATABASE_URL", sqlalchemy_dsn)
    yield sqlalchemy_dsn


@pytest.mark.asyncio
async def test_retrieve_finds_matching_chunk(retrieval_env):
    results = await retrieve(
        "JWT auth",
        "test-repo",
        FakeEmbedding(),
        top_k=20,
        token_budget=6000,
    )

    assert len(results) >= 1
    assert any("JWT" in chunk.text or "jwt" in chunk.text for chunk in results)


@pytest.mark.asyncio
async def test_retrieve_respects_token_budget(retrieval_env):
    results = await retrieve(
        "JWT auth",
        "test-repo",
        FakeEmbedding(),
        top_k=20,
        token_budget=100,
    )

    encoding = tiktoken.encoding_for_model("gpt-4")
    total_tokens = sum(len(encoding.encode(chunk.text)) for chunk in results)

    assert len(results) <= 2
    assert total_tokens <= 100


@pytest.mark.asyncio
async def test_retrieve_rrf_orders_by_score(retrieval_env):
    results = await retrieve(
        "JWT auth",
        "test-repo",
        FakeEmbedding(),
        top_k=20,
        token_budget=6000,
    )

    assert len(results) >= 2
    assert results[0].score > results[-1].score
    # Results must be returned in descending score order.
    assert all(
        results[i].score >= results[i + 1].score
        for i in range(len(results) - 1)
    )
