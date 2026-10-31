"""Tests for the native Anthropic Messages provider."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from enten.config import Settings
from enten.llm import AnthropicProvider, Message, ToolSchema, ToolUse
from enten.llm.errors import LLMAuthenticationError

BASE_URL = "https://api.anthropic.test"
MODEL = "claude-sonnet-5.5"


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "llm_provider": "anthropic",
            "anthropic_base_url": BASE_URL,
            "anthropic_api_key": "sk-ant-test",
            "anthropic_version": "2023-06-01",
            "llm_default_model": MODEL,
        }
    )


def _tool() -> ToolSchema:
    return ToolSchema(
        name="submit_review",
        description="Submit review findings.",
        parameters={
            "type": "object",
            "properties": {"findings": {"type": "array"}},
            "required": ["findings"],
        },
    )


@respx.mock
async def test_complete_maps_text_and_tool_use(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{BASE_URL}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": MODEL,
                "content": [
                    {"type": "text", "text": "Review complete."},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "submit_review",
                        "input": {"findings": [{"file": "app.py", "line": 3}]},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        ),
    )

    provider = AnthropicProvider(_settings())
    response = await provider.complete(
        [Message(role="user", content="review this")],
        tools=[_tool()],
    )

    assert response.content == "Review complete."
    assert response.tool_calls == [
        ToolUse(
            id="tool-1",
            name="submit_review",
            arguments={"findings": [{"file": "app.py", "line": 3}]},
        )
    ]
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5

    body = json.loads(route.calls.last.request.content)
    assert "system" not in body
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "review this"}]}
    ]
    assert body["tools"][0]["input_schema"] == _tool().parameters


@respx.mock
async def test_complete_serializes_tool_results_and_system(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(f"{BASE_URL}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": MODEL,
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 4, "output_tokens": 1},
            },
        ),
    )
    tool = ToolUse(id="tool-1", name="submit_review", arguments={"ok": True})
    provider = AnthropicProvider(_settings())

    await provider.complete(
        [
            Message(role="system", content="system rules"),
            Message(role="assistant", content="checking", tool_calls=[tool]),
            Message(role="tool", content="result", tool_call_id="tool-1"),
        ],
    )

    body = json.loads(route.calls.last.request.content)
    assert body["system"] == "system rules"
    assert body["messages"][0] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "checking"},
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "submit_review",
                "input": {"ok": True},
            },
        ],
    }
    assert body["messages"][1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "result"}
        ],
    }


@respx.mock
async def test_authentication_failure_is_typed(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BASE_URL}/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": "bad key"}),
    )
    provider = AnthropicProvider(_settings())

    with pytest.raises(LLMAuthenticationError):
        await provider.complete([Message(role="user", content="hello")])


@respx.mock
async def test_stream_yields_text_deltas(respx_mock: respx.MockRouter) -> None:
    stream = (
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'
    )
    respx_mock.post(f"{BASE_URL}/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text=stream,
            headers={"content-type": "text/event-stream"},
        ),
    )
    provider = AnthropicProvider(_settings())

    deltas = [delta async for delta in provider.stream([Message(role="user", content="hi")])]

    assert [delta.content for delta in deltas] == ["hi"]
