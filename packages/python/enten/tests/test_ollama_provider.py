"""Tests for :class:`~enten.llm.OllamaProvider`.

All HTTP calls are mocked with ``respx``; the test suite never talks to a
real Ollama daemon.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from enten.config import Settings
from enten.llm import LLMUnavailableError, Message, OllamaProvider, ToolSchema, ToolUse

BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5"


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "llm_ollama_base_url": BASE_URL,
            "llm_default_model": DEFAULT_MODEL,
        }
    )


@respx.mock
async def test_complete_returns_content(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "hi"}, "done": True, "model": "qwen2.5"},
        ),
    )

    provider = OllamaProvider(_settings())
    response = await provider.complete([Message(role="user", content="hello")])

    assert response.content == "hi"
    assert response.model == "qwen2.5"


@respx.mock
async def test_complete_maps_tool_call(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_weather",
                                "arguments": {"city": "NYC"},
                            },
                        },
                    ],
                },
                "done": True,
                "model": "qwen2.5",
            },
        ),
    )

    provider = OllamaProvider(_settings())
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


@respx.mock
async def test_complete_serializes_assistant_tool_history(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(f"{BASE_URL}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "done"}, "done": True, "model": "qwen2.5"},
        ),
    )
    tool = ToolUse(id="call_1", name="lookup", arguments={"path": "a.py"})
    provider = OllamaProvider(_settings())

    await provider.complete(
        [
            Message(role="assistant", content="checking", tool_calls=[tool]),
            Message(role="tool", content="contents", tool_call_id=tool.id),
        ],
        max_tokens=321,
    )

    payload = route.calls.last.request.content
    assert payload is not None
    body = json.loads(payload)
    assert body["options"] == {"num_predict": 321}
    assert body["messages"][0]["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": {"path": "a.py"},
    }


@respx.mock
async def test_complete_raises_on_missing_model(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/api/chat").mock(
        return_value=httpx.Response(404, text="model not found"),
    )

    provider = OllamaProvider(_settings())
    with pytest.raises(LLMUnavailableError):
        await provider.complete([Message(role="user", content="hello")])


@respx.mock
def test_count_tokens_falls_back_when_show_fails(respx_mock: respx.MockRouter) -> None:
    """``count_tokens`` attempts ``/api/show`` and falls back to a heuristic."""
    respx_mock.post(f"{BASE_URL}/api/show").mock(
        return_value=httpx.Response(404, text="model not found"),
    )

    provider = OllamaProvider(_settings())
    text = "hello world"
    tokens = provider.count_tokens([Message(role="user", content=text)])

    assert tokens == len(text) // 4
