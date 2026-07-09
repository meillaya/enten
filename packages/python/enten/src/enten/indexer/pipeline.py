from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import tiktoken
from sqlalchemy import text

from enten.config import Settings
from enten.db import get_session
from enten.embeddings import EmbeddingProvider
from enten.indexer import chunker


@dataclass(frozen=True)
class IndexStats:
    indexed: int
    updated: int
    skipped: int


_UPSERT_SQL = """
INSERT INTO vec.chunks (
    id, repo_id, path, lang, start_line, end_line, text,
    embedding, symbols, content_hash, indexed_at
) VALUES (
    :id, :repo_id, :path, :lang, :start_line, :end_line, :text,
    (:embedding)::vector, (:symbols)::jsonb, :content_hash, now()
)
ON CONFLICT (id) DO UPDATE SET
    repo_id = EXCLUDED.repo_id,
    path = EXCLUDED.path,
    lang = EXCLUDED.lang,
    start_line = EXCLUDED.start_line,
    end_line = EXCLUDED.end_line,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding,
    symbols = EXCLUDED.symbols,
    content_hash = EXCLUDED.content_hash,
    indexed_at = now()
"""


def _chunk_id(repo_id: str, chunk: chunker.Chunk) -> str:
    payload = f"{repo_id}:{chunk.path}:{chunk.start_line}:{chunk.end_line}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _token_count(text: str) -> int:
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))


def _symbols_to_json(symbols: list[chunker.Symbol]) -> str:
    return json.dumps(
        [
            {
                "kind": symbol.kind,
                "name": symbol.name,
                "line": symbol.line,
                "parent": symbol.parent,
            }
            for symbol in symbols
        ]
    )


async def index_repo_for_repo(
    repo_path: str,
    repo_id: str,
    embedding: EmbeddingProvider,
) -> IndexStats:
    settings = Settings(_env_file=None)
    chunks = chunker.chunk(Path(repo_path))

    total_tokens = sum(_token_count(chunk.text) for chunk in chunks)
    if total_tokens > settings.max_pr_diff_tokens:
        raise ValueError(
            f"repo token count {total_tokens} exceeds MAX_PR_DIFF_TOKENS "
            f"{settings.max_pr_diff_tokens}"
        )

    batch_size = settings.embedding_batch_size
    indexed = 0
    updated = 0
    skipped = 0
    pending: list[chunker.Chunk] = []

    async with get_session() as session:
        for chunk in chunks:
            chunk_id = _chunk_id(repo_id, chunk)
            existing = await session.scalar(
                text("SELECT content_hash FROM vec.chunks WHERE id = :id"),
                {"id": chunk_id},
            )
            content_hash = _content_hash(chunk.text)
            if existing == content_hash:
                skipped += 1
                continue
            if existing is None:
                indexed += 1
            else:
                updated += 1
            pending.append(chunk)

        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            texts = [c.text for c in batch]
            embeddings = await embedding.embed_batch(texts)
            values = []
            for c, emb in zip(batch, embeddings, strict=True):
                values.append(
                    {
                        "id": _chunk_id(repo_id, c),
                        "repo_id": repo_id,
                        "path": c.path,
                        "lang": c.lang,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "text": c.text,
                        "embedding": json.dumps(emb),
                        "symbols": _symbols_to_json(c.symbols),
                        "content_hash": _content_hash(c.text),
                    }
                )
            await session.execute(text(_UPSERT_SQL), values)

        await session.commit()

    return IndexStats(indexed=indexed, updated=updated, skipped=skipped)
