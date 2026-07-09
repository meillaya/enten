"""Tests for :class:`~enten.llm.OpenAICompatProvider`.

All HTTP calls are mocked with ``respx``; the suite never talks to a real
OpenAI-compatible backend.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from enten.config import Settings
from enten.llm import LLMProvider, Message, OpenAICompatProvider, ToolSchema
from enten.llm.errors import LLMAuthenticationError

BASE_URL = "https://api.openai.test/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_openai_base_url=BASE_URL,
        llm_openai_api_key="sk-test",
        llm_default_model=DEFAULT_MODEL,
    )


def test_provider_is_runtime_checkable_instance() -> None:
    """``OpenAICompatProvider`` satisfies ``isinstance(..., LLMProvider)``."""
    provider = OpenAICompatProvider(_settings())
    assert isinstance(provider, LLMProvider)


@respx.mock
async def test_complete_returns_content(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1},
            },
        ),
    )

    provider = OpenAICompatProvider(_settings())
    response = await provider.complete([Message(role="user", content="hello")])

    assert response.content == "hi"
    assert response.usage.input_tokens == 4
    assert response.usage.output_tokens == 1
    assert response.model == DEFAULT_MODEL


@respx.mock
async def test_complete_maps_tool_call(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "NYC"}',
                                    },
                                },
                            ],
                        },
                    },
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "gpt-4o",
            },
        ),
    )

    provider = OpenAICompatProvider(_settings())
    response = await provider.complete(
        [Message(role="user", content="what's the weather?")],
        tools=[
            ToolSchema(
                name="get_weather",
                description="Get the weather for a city.",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            ),
        ],
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_weather"
    assert response.tool_calls[0].arguments == {"city": "NYC"}
    assert response.model == "gpt-4o"


@respx.mock
async def test_stream_yields_deltas(respx_mock: respx.MockRouter) -> None:
    chunk = (
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx_mock.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=chunk,
            headers={"content-type": "text/event-stream"},
        ),
    )

    provider = OpenAICompatProvider(_settings())
    deltas = [
        delta
        async for delta in provider.stream([Message(role="user", content="hello")])
    ]

    assert len(deltas) >= 1
    assert deltas[0].content == "hi"


@respx.mock
async def test_complete_raises_authentication_error(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(f"{BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(401, text="invalid api key"),
    )

    provider = OpenAICompatProvider(_settings())
    with pytest.raises(LLMAuthenticationError):
        await provider.complete([Message(role="user", content="hello")])


@respx.mock
async def test_complete_retries_429_and_succeeds(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_sleep(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    route = respx_mock.post(f"{BASE_URL}/chat/completions").mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                },
            ),
        ],
    )

    provider = OpenAICompatProvider(_settings())
    response = await provider.complete([Message(role="user", content="hello")])

    assert response.content == "ok"
    assert route.call_count == 2


def test_count_tokens_returns_positive_int() -> None:
    """``count_tokens`` returns a positive integer for a simple message."""
    provider = OpenAICompatProvider(_settings())
    tokens = provider.count_tokens([Message(role="user", content="hello world")])

    assert isinstance(tokens, int)
    assert tokens > 0
