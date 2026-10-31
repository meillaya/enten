"""Factory for the configured LLM provider."""

from __future__ import annotations

from enten.config import LLMProvider as LLMProviderEnum
from enten.config import Settings
from enten.llm.anthropic import AnthropicProvider
from enten.llm.ollama import OllamaProvider
from enten.llm.openai_compat import OpenAICompatProvider
from enten.llm.types import LLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    """Return the provider selected by ``LLM_PROVIDER``."""
    if settings.llm_provider == LLMProviderEnum.OPENAI_COMPAT:
        return OpenAICompatProvider(settings)
    if settings.llm_provider == LLMProviderEnum.ANTHROPIC:
        return AnthropicProvider(settings)
    if settings.llm_provider == LLMProviderEnum.OLLAMA:
        return OllamaProvider(settings)
    raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")


__all__ = ["create_llm_provider"]
