"""OpenAI-compatible LLM provider.

Works with OpenAI, OpenRouter, LM Studio, llama.cpp server, vLLM, and any
other backend that exposes the ``/chat/completions`` endpoint. Only
``httpx`` is used; the official ``openai`` SDK is not required.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
import tiktoken

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


class OpenAICompatProvider:
    """Provider for any OpenAI-compatible chat-completions endpoint."""

    name = "openai-compat"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_openai_base_url.rstrip("/")
        self.api_key = settings.llm_openai_api_key
        self.default_model = settings.llm_default_model

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=60.0,
        )

    def _resolve_model(self, model: str | None) -> str:
        return model or self.default_model

    @staticmethod
    def _message_to_openai(message: Message) -> dict[str, Any]:
        """Convert a normalized Message into an OpenAI chat message dict."""
        content: str | list[dict[str, Any]]
        raw_content = message.content

        if isinstance(raw_content, str):
            content = raw_content
        else:
            content = []
            for part in raw_content:
                if part.type == "text":
                    content.append({"type": "text", "text": part.text or ""})
                elif part.type == "image":
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": part.image_url or ""},
                        },
                    )

        out: dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_call_id:
            out["tool_call_id"] = message.tool_call_id
        if message.name:
            out["name"] = message.name
        return out

    @staticmethod
    def _tool_to_openai(tool: ToolSchema) -> dict[str, Any]:
        """Convert a ToolSchema into OpenAI's function-tool shape."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    @staticmethod
    def _text_from_messages(messages: list[Message]) -> str:
        """Flatten messages into a single string for token estimation."""
        parts: list[str] = []
        for message in messages:
            content = message.content
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.extend(part.text or "" for part in content)
        return "\n".join(parts)

    def _payload(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        model: str,
        stream: bool,
        temperature: float,
        opts: dict[str, Any],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [self._message_to_openai(m) for m in messages],
            "stream": stream,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = [self._tool_to_openai(t) for t in tools]

        # Pass through provider-specific options (e.g. max_tokens) without
        # overwriting the fields we have already normalized.
        for key, value in opts.items():
            if key not in body:
                body[key] = value

        return body

    @staticmethod
    def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
        """Parse tool arguments from a JSON string or dict."""
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            try:
                return json.loads(raw_arguments)
            except json.JSONDecodeError:
                return {}
        return {}

    @classmethod
    def _tool_calls_from_openai(
        cls,
        raw_tool_calls: list[dict[str, Any]],
    ) -> list[ToolUse]:
        """Map OpenAI tool_calls to normalized ToolUse objects."""
        tool_calls: list[ToolUse] = []
        for raw in raw_tool_calls:
            fn = raw.get("function") or {}
            tool_calls.append(
                ToolUse(
                    id=raw.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=cls._parse_tool_arguments(fn.get("arguments")),
                ),
            )
        return tool_calls

    async def _post_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        """POST to /chat/completions with retries for rate limits and 5xx."""
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.NetworkError as exc:
                last_error = exc
                if attempt == 3:
                    raise LLMUnavailableError(
                        f"OpenAI-compatible backend unreachable: {exc}",
                    ) from exc
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if response.status_code == 401:
                raise LLMAuthenticationError(
                    "OpenAI-compatible backend rejected the API key",
                )

            if response.status_code == 429:
                if attempt == 3:
                    raise LLMRateLimitError(
                        "OpenAI-compatible backend rate limit persisted after retries",
                    )
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if response.status_code >= 500:
                if attempt == 3:
                    raise LLMUnavailableError(
                        f"OpenAI-compatible backend returned {response.status_code}",
                    )
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            # Do not retry other 4xx errors.
            response.raise_for_status()
            return response

        raise LLMUnavailableError(
            "OpenAI-compatible backend unavailable",
        ) from last_error

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        **opts: Any,
    ) -> LLMResponse:
        """Issue a non-streaming completion request."""
        resolved_model = self._resolve_model(model)
        payload = self._payload(
            messages,
            tools,
            resolved_model,
            stream=False,
            temperature=temperature,
            opts=opts,
        )

        logger.debug(
            "openai_compat completion request",
            model=resolved_model,
            message_count=len(messages),
            estimated_tokens=self.count_tokens(messages),
        )

        response = await self._post_with_retry(payload)
        data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""

        tool_calls = self._tool_calls_from_openai(message.get("tool_calls") or [])

        raw_usage = data.get("usage") or {}
        usage = Usage(
            input_tokens=raw_usage.get("prompt_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0),
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", resolved_model),
            raw=data,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        model: str | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        """Issue a streaming completion request and yield normalized deltas."""
        resolved_model = self._resolve_model(model)
        payload = self._payload(
            messages,
            tools,
            resolved_model,
            stream=True,
            temperature=opts.pop("temperature", 0.0),
            opts=opts,
        )

        logger.debug(
            "openai_compat streaming request",
            model=resolved_model,
            message_count=len(messages),
            estimated_tokens=self.count_tokens(messages),
        )

        async with self._client.stream(
            "POST",
            "/chat/completions",
            json=payload,
        ) as response:
            if response.status_code == 401:
                raise LLMAuthenticationError(
                    "OpenAI-compatible backend rejected the API key",
                )
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line:
                    continue

                if line.startswith("data: "):
                    line = line[6:]

                if line == "[DONE]":
                    break

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                choice = (data.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                delta_content = delta.get("content") or ""

                delta_tool_calls: list[ToolUse] | None = None
                raw_tool_calls = delta.get("tool_calls")
                if raw_tool_calls:
                    delta_tool_calls = self._tool_calls_from_openai(raw_tool_calls)

                yield LLMDelta(content=delta_content, tool_calls=delta_tool_calls)

    def count_tokens(self, messages: list[Message]) -> int:
        """Return the approximate token count for ``messages``.

        Uses ``tiktoken`` for ``gpt-4`` when available; otherwise falls back
        to a rough character-based heuristic.
        """
        text = self._text_from_messages(messages)

        try:
            enc = tiktoken.encoding_for_model("gpt-4")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4
