"""Tests for the LLM provider factory and worker generator selection."""

from __future__ import annotations

import pytest

from enten._worker_runtime import WorkerConfigurationError, build_findings_generator
from enten.config import Settings
from enten.llm import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatProvider,
    create_llm_provider,
)
from enten.review.llm import LLMFindingsGenerator
from enten.review.local import LocalFindingsGenerator


def _settings(**kwargs: object) -> Settings:
    return Settings.model_validate(kwargs)


def test_factory_selects_openai_compatible_provider() -> None:
    provider = create_llm_provider(_settings(llm_provider="openai-compat"))
    assert isinstance(provider, OpenAICompatProvider)


def test_factory_selects_anthropic_provider() -> None:
    provider = create_llm_provider(_settings(llm_provider="anthropic"))
    assert isinstance(provider, AnthropicProvider)


def test_factory_selects_ollama_provider() -> None:
    provider = create_llm_provider(_settings(llm_provider="ollama"))
    assert isinstance(provider, OllamaProvider)


def test_local_generator_is_the_default() -> None:
    generator = build_findings_generator(_settings())
    assert isinstance(generator, LocalFindingsGenerator)


def test_llm_generator_requires_an_api_key() -> None:
    settings = _settings(
        review_generator="llm",
        llm_provider="openai-compat",
        llm_openai_api_key=None,
    )
    with pytest.raises(WorkerConfigurationError):
        build_findings_generator(settings)


def test_llm_generator_builds_with_openrouter_settings() -> None:
    settings = _settings(
        review_generator="llm",
        llm_provider="openai-compat",
        llm_openai_base_url="https://openrouter.ai/api/v1",
        llm_openai_api_key="sk-or-test",
        llm_default_model="deepseek/deepseek-v4.1-flash-1017",
    )
    generator = build_findings_generator(settings)
    assert isinstance(generator, LLMFindingsGenerator)
