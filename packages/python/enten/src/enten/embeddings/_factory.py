from __future__ import annotations

from enten.config import Settings
from enten.embeddings._openai_compat import OpenAICompatEmbeddings
from enten.embeddings._protocol import EmbeddingProvider


def get_embeddings(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai-compat":
        base_url = settings.embedding_base_url or settings.llm_openai_base_url
        api_key = settings.embedding_api_key or settings.llm_openai_api_key
        if not base_url:
            raise ValueError("embedding base_url is required")
        return OpenAICompatEmbeddings(
            base_url=base_url,
            api_key=api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
            timeout=settings.embedding_timeout,
        )
    raise ValueError(f"unknown embedding provider: {settings.embedding_provider}")
