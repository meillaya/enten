from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...
