"""Hybrid retrieval layer: BM25 + pgvector with RRF reranking.

Public API:
    RetrievedChunk   — a chunk returned by the retrieval pipeline.
    EmbeddingProvider — structural protocol for embedding backends.
    retrieve          — async hybrid search over vec.chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import tiktoken
from rank_bm25 import BM25Okapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from enten.config import Settings
from enten.retrieval.rerank import LLMRerank


@dataclass
class RetrievedChunk:
    """A candidate chunk produced by hybrid retrieval."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str
    score: float
    vector_distance: float
    bm25_score: float
    tokens: int


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal protocol for an embedding backend used by retrieval."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return an embedding vector for every input text."""
        ...


def _vector_literal(vector: Sequence[float]) -> str:
    """Return a PostgreSQL vector literal string."""
    return "[" + ",".join(str(v) for v in vector) + "]"


def _count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    """Count GPT-4 tokens for *text*."""
    return len(encoding.encode(text))


def _rrf_score(vector_rank: int, bm25_rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion with the configured rank weights."""
    return 0.6 * (1 / (k + vector_rank)) + 0.4 * (1 / (k + bm25_rank))


def _select_within_budget(
    candidates: Sequence[RetrievedChunk],
    *,
    top_k: int,
    token_budget: int,
) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    used_tokens = 0
    for chunk in candidates:
        if used_tokens + chunk.tokens > token_budget:
            continue
        selected.append(chunk)
        used_tokens += chunk.tokens
        if len(selected) >= top_k:
            break
    return selected


async def retrieve(
    query: str,
    repo_id: str,
    embedding: EmbeddingProvider,
    *,
    top_k: int = 20,
    token_budget: int = 6000,
) -> list[RetrievedChunk]:
    """Retrieve the most relevant chunks for *query* using BM25 + pgvector.

    Steps:
        1. Embed the query.
        2. Fetch ``top_k * 2`` nearest neighbours from ``vec.chunks``.
        3. Score the same candidate set with in-process BM25.
        4. Fuse ranks with RRF (vector weight 0.6, BM25 weight 0.4).
        5. Sort by fused score and greedily truncate to *token_budget*.
        6. Cap the result at *top_k* chunks.
    """
    settings = Settings()
    query_vectors = await embedding.embed_batch([query])
    query_vector = query_vectors[0]

    limit = top_k * 2
    vector_sql = text(
        """
        SELECT id, path, start_line, end_line, text,
               embedding <=> CAST(:embedding AS vector) AS distance
        FROM vec.chunks
        WHERE repo_id = :repo_id
        ORDER BY distance
        LIMIT :limit
        """
    ).bindparams(
        embedding=_vector_literal(query_vector),
        repo_id=repo_id,
        limit=limit,
    )

    engine = create_async_engine(settings.database_url, future=True, echo=False)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(vector_sql)).mappings().all()
    finally:
        await engine.dispose()

    if not rows:
        return []

    texts = [row["text"] for row in rows]
    tokenized_corpus = [doc.split() for doc in texts]
    tokenized_query = query.split()
    bm25 = BM25Okapi(tokenized_corpus)
    bm25_scores = bm25.get_scores(tokenized_query)

    # Ranks are 0-based: lower is better.
    vector_ranks = {row["id"]: rank for rank, row in enumerate(rows)}
    bm25_ranking = sorted(
        range(len(rows)),
        key=lambda idx: bm25_scores[idx],
        reverse=True,
    )
    bm25_ranks = {rows[idx]["id"]: rank for rank, idx in enumerate(bm25_ranking)}

    encoding = tiktoken.encoding_for_model("gpt-4")
    candidates: list[RetrievedChunk] = []
    for row in rows:
        chunk_id = row["id"]
        candidates.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                path=row["path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                text=row["text"],
                score=_rrf_score(vector_ranks[chunk_id], bm25_ranks[chunk_id]),
                vector_distance=float(row["distance"]),
                bm25_score=float(bm25_scores[vector_ranks[chunk_id]]),
                tokens=_count_tokens(row["text"], encoding),
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)

    reranker = LLMRerank()
    candidates = await reranker.rerank(query, candidates)

    return _select_within_budget(candidates, top_k=top_k, token_budget=token_budget)


__all__ = [
    "EmbeddingProvider",
    "RetrievedChunk",
    "retrieve",
]
