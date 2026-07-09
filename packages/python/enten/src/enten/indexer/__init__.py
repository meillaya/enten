"""Tree-sitter based multi-language indexer.

Public API:
    Symbol   — a named symbol inside a chunk.
    Chunk    — a scope-aware slice of source code.
    index_repo  — chunk every source file in a repository.
    index_diff  — chunk only files changed between two Git commits.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from enten.indexer.chunker import Chunk as _Chunk
from enten.indexer.chunker import Symbol as _Symbol
from enten.indexer.chunker import index_diff as _index_diff
from enten.indexer.chunker import index_repo as _index_repo


class Symbol(BaseModel):
    """A named symbol discovered inside a chunk."""

    kind: str
    name: str
    line: int
    parent: str | None = None


class Chunk(BaseModel):
    """A scope-aware source-code chunk."""

    chunk_id: str
    path: str
    lang: Literal["python", "typescript"]
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    text: str
    symbols: list[Symbol] = Field(default_factory=list)
    scope_kind: Literal["module", "function", "class", "block"]


def _to_public(chunk: _Chunk) -> Chunk:
    return Chunk(
        chunk_id=chunk.chunk_id,
        path=chunk.path,
        lang=chunk.lang,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        start_byte=chunk.start_byte,
        end_byte=chunk.end_byte,
        text=chunk.text,
        symbols=[Symbol(**s.model_dump()) for s in chunk.symbols],
        scope_kind=chunk.scope_kind,
    )


def index_repo(repo_path: str, language: Literal["python", "typescript"]) -> list[Chunk]:
    """Index every source file under *repo_path* for the given language."""
    return [_to_public(c) for c in _index_repo(Path(repo_path), language)]


def index_diff(
    repo_path: str,
    language: Literal["python", "typescript"],
    base_sha: str,
    head_sha: str,
) -> list[Chunk]:
    """Index only the files changed between *base_sha* and *head_sha*."""
    return [_to_public(c) for c in _index_diff(Path(repo_path), language, base_sha, head_sha)]
