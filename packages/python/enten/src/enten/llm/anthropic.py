"""Native Anthropic Messages API provider.

Anthropic does not expose the OpenAI chat-completions wire format. This
provider maps the normalized request and response models onto the Messages
API, including tool use and token usage.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from enten.config import Settings
from enten.llm.errors import (
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from enten.llm.types import (
    LLMDelta,
    LLMResponse,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 4096
MAX_RETRIES = 3


class AnthropicProvider:
    """Provider for the native Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.anthropic_base_url.rstrip("/")
        self.default_model = settings.llm_default_model
        headers = {
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        }
        if settings.anthropic_api_key:
            headers["x-api-key"] = settings.anthropic_api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=90.0,
        )

    def _resolve_model(self, model: str | None) -> str:
        return model or self.default_model

    @staticmethod
    def _text(content: str | list[Any]) -> str:
        if isinstance(content, str):
            return content
        return "\n".join(part.text or "" for part in content if part.text)

    @classmethod
    def _messages(cls, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Split system text from the alternating user and assistant turns."""
        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []

        def append(role: str, block: dict[str, Any]) -> None:
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"].append(block)
                return
            turns.append({"role": role, "content": [block]})

        for message in messages:
            text = cls._text(message.content)
            if message.role == "system":
                if text:
                    system_parts.append(text)
                continue
            if message.role == "tool":
                append(
                    "user",
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "unknown",
                        "content": text,
                    },
                )
                continue
            if message.role == "assistant":
                if text:
                    append("assistant", {"type": "text", "text": text})
                for tool in message.tool_calls:
                    append(
                        "assistant",
                        {
                            "type": "tool_use",
                            "id": tool.id,
                            "name": tool.name,
                            "input": tool.arguments,
                        },
                    )
                continue
            append("user", {"type": "text", "text": text})

        return "\n\n".join(system_parts), turns

    @staticmethod
    def _tools(tools: list[ToolSchema] | None) -> list[dict[str, Any]]:
        if not tools:
            return []
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    def _payload(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        model: str,
        stream: bool,
        opts: dict[str, Any],
    ) -> dict[str, Any]:
        system, turns = self._messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": opts.get("max_tokens", DEFAULT_MAX_TOKENS),
            "temperature": opts.get("temperature", 0.0),
            "messages": turns,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._tools(tools)
        return payload

    async def _post_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._client.post("/v1/messages", json=payload)
            except httpx.NetworkError as exc:
                last_error = exc
                if attempt == MAX_RETRIES:
                    raise LLMUnavailableError(
                        f"Anthropic backend unreachable: {exc}",
                    ) from exc
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if response.status_code == 401:
                raise LLMAuthenticationError("Anthropic backend rejected the API key")

            if response.status_code == 429:
                if attempt == MAX_RETRIES:
                    raise LLMRateLimitError(
                        "Anthropic rate limit persisted after retries",
                    )
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if response.status_code >= 500:
                if attempt == MAX_RETRIES:
                    raise LLMUnavailableError(
                        f"Anthropic backend returned {response.status_code}",
                    )
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            response.raise_for_status()
            return response

        raise LLMUnavailableError("Anthropic backend unavailable") from last_error

    @staticmethod
    def _parse_response(data: dict[str, Any], fallback_model: str) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolUse] = []
        for block in data.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                tool_calls.append(
                    ToolUse(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
        raw_usage = data.get("usage") or {}
        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=int(raw_usage.get("input_tokens", 0)),
                output_tokens=int(raw_usage.get("output_tokens", 0)),
            ),
            model=str(data.get("model", fallback_model)),
            raw=data,
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        model: str | None = None,
        **opts: Any,
    ) -> LLMResponse:
        """Issue one non-streaming Messages request."""
        resolved_model = self._resolve_model(model)
        payload = self._payload(messages, tools, resolved_model, False, opts)
        logger.debug(
            "anthropic completion request",
            model=resolved_model,
            message_count=len(messages),
        )
        response = await self._post_with_retry(payload)
        return self._parse_response(response.json(), resolved_model)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        model: str | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        """Yield text deltas from the Messages streaming API."""
        resolved_model = self._resolve_model(model)
        payload = self._payload(messages, tools, resolved_model, True, opts)
        async with self._client.stream("POST", "/v1/messages", json=payload) as response:
            if response.status_code == 401:
                raise LLMAuthenticationError("Anthropic backend rejected the API key")
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("type") != "content_block_delta":
                    continue
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield LLMDelta(content=str(delta.get("text") or ""))

    def count_tokens(self, messages: list[Message]) -> int:
        """Return a rough token count for the conversation."""
        text = "\n".join(self._text(message.content) for message in messages)
        return len(text) // 4
