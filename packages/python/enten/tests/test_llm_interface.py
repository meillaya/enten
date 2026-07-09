"""Tests for the LLM provider interface and Pydantic schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from enten.llm import (
    LLMProvider,
    LLMResponse,
    Message,
    StubProvider,
    ToolUse,
    Usage,
)


def test_stub_providers_are_runtime_checkable_instances() -> None:
    """Two StubProvider instances satisfy ``isinstance(..., LLMProvider)``."""
    provider_one = StubProvider()
    provider_two = StubProvider(content="second")

    assert isinstance(provider_one, LLMProvider)
    assert isinstance(provider_two, LLMProvider)


def test_llm_response_model_validate_accepts_full_dict() -> None:
    """``LLMResponse.model_validate`` accepts a complete response dict."""
    data = {
        "content": "hello",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "get_weather",
                "arguments": {"city": "Berlin"},
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "model": "gpt-4",
        "raw": {"choices": []},
    }

    response = LLMResponse.model_validate(data)

    assert response.content == "hello"
    assert len(response.tool_calls) == 1
    assert isinstance(response.tool_calls[0], ToolUse)
    assert response.tool_calls[0].name == "get_weather"
    assert isinstance(response.usage, Usage)
    assert response.usage.input_tokens == 10
    assert response.model == "gpt-4"


def test_llm_response_model_validate_rejects_missing_required() -> None:
    """``LLMResponse.model_validate`` raises when required fields are missing."""
    with pytest.raises(ValidationError):
        LLMResponse.model_validate({"content": "hi"})


def test_count_tokens_returns_reasonable_int() -> None:
    """``StubProvider.count_tokens`` returns a positive integer."""
    provider = StubProvider()
    messages = [Message(role="user", content="hi there")]

    count = provider.count_tokens(messages)

    assert isinstance(count, int)
    assert count == 4
