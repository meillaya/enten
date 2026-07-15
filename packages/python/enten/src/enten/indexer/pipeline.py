from __future__ import annotations

import hashlib
import json
import subprocess
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


def _chunk_id(repo_id: str, snapshot_id: str, chunk: chunker.Chunk) -> str:
    payload = ":".join(
        (
            repo_id,
            snapshot_id,
            chunk.path,
            str(chunk.start_line),
            str(chunk.end_line),
            _content_hash(chunk.text),
        )
    )
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


def _resolve_snapshot_id(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "working-tree"
    return result.stdout.strip() or "working-tree"


async def index_repo_for_repo(
    repo_path: str,
    repo_id: str,
    embedding: EmbeddingProvider,
    *,
    snapshot_id: str | None = None,
) -> IndexStats:
    settings = Settings.model_validate({})
    chunks = chunker.chunk(Path(repo_path))
    resolved_snapshot = snapshot_id or _resolve_snapshot_id(Path(repo_path))

    batch_size = settings.embedding_batch_size
    indexed = 0
    updated = 0
    skipped = 0
    pending: list[tuple[str, chunker.Chunk]] = []
    incoming_ids = {_chunk_id(repo_id, resolved_snapshot, item) for item in chunks}

    async with get_session() as session:
        existing_rows = (
            await session.execute(
                text(
                    "SELECT id, path, start_line, end_line, content_hash "
                    "FROM vec.chunks WHERE repo_id = :repo_id"
                ),
                {"repo_id": repo_id},
            )
        ).mappings()
        existing_by_id = {row["id"]: row for row in existing_rows}
        existing_by_location = {
            (row["path"], row["start_line"], row["end_line"]): row
            for row in existing_by_id.values()
        }

        for chunk in chunks:
            chunk_id = _chunk_id(repo_id, resolved_snapshot, chunk)
            content_hash = _content_hash(chunk.text)
            existing = existing_by_id.get(chunk_id)
            if existing is not None and existing["content_hash"] == content_hash:
                skipped += 1
                continue
            location = (chunk.path, chunk.start_line, chunk.end_line)
            if location in existing_by_location:
                updated += 1
            else:
                indexed += 1
            pending.append((chunk_id, chunk))

        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            texts = [item.text for _, item in batch]
            embeddings = await embedding.embed_batch(texts)
            values = []
            for (chunk_id, item), emb in zip(batch, embeddings, strict=True):
                values.append(
                    {
                        "id": chunk_id,
                        "repo_id": repo_id,
                        "path": item.path,
                        "lang": item.lang,
                        "start_line": item.start_line,
                        "end_line": item.end_line,
                        "text": item.text,
                        "embedding": json.dumps(emb),
                        "symbols": _symbols_to_json(item.symbols),
                        "content_hash": _content_hash(item.text),
                    }
                )
            await session.execute(text(_UPSERT_SQL), values)

        if incoming_ids:
            await session.execute(
                text(
                    "DELETE FROM vec.chunks WHERE repo_id = :repo_id "
                    "AND NOT (id = ANY(CAST(:ids AS text[])))"
                ),
                {"repo_id": repo_id, "ids": sorted(incoming_ids)},
            )
        else:
            await session.execute(
                text("DELETE FROM vec.chunks WHERE repo_id = :repo_id"),
                {"repo_id": repo_id},
            )

        await session.commit()

    return IndexStats(indexed=indexed, updated=updated, skipped=skipped)
