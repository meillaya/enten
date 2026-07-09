"""Reranking interface and implementations for the retrieval layer.

This module is intentionally minimal for v1. The LLM-based reranker is a
placeholder that returns its input unchanged; T30+ will wire a real LLM
judge here.
"""

from __future__ import annotations

from typing import Any


class LLMRerank:
    """Stub reranker: returns the candidate list unchanged.

    A real implementation in T30+ will call an LLM to score each chunk's
    relevance to the query and reorder accordingly. Using this stub keeps
    the retrieval API stable while avoiding silent LLM calls in v1.
    """

    async def rerank(
        self,
        query: str,  # noqa: ARG002
        chunks: list[Any],
    ) -> list[Any]:
        """Return *chunks* without modification."""
        return chunks


__all__ = ["LLMRerank"]
