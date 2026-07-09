from __future__ import annotations

from enten.embeddings._errors import EmbeddingUnavailableError
from enten.embeddings._factory import get_embeddings
from enten.embeddings._openai_compat import OpenAICompatEmbeddings
from enten.embeddings._protocol import EmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "OpenAICompatEmbeddings",
    "get_embeddings",
    "EmbeddingUnavailableError",
]
