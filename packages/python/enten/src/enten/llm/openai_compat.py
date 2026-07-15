"""OpenAI-compatible LLM provider.

Works with OpenAI, OpenRouter, LM Studio, llama.cpp server, vLLM, and any
other backend that exposes the ``/chat/completions`` endpoint. Only
``httpx`` is used; the official ``openai`` SDK is not required.
"""

from __future__ import annotations

import asyncio
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
from enten.llm.openai_wire import (
    messages_text,
    parse_stream_line,
    request_payload,
    tool_calls_from_openai,
)
from enten.llm.types import (
    LLMDelta,
    LLMResponse,
    Message,
    ToolSchema,
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

    def _payload(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        model: str,
        stream: bool,
        temperature: float,
        opts: dict[str, Any],
    ) -> dict[str, Any]:
        return request_payload(messages, tools, model, stream, temperature, opts)

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

        tool_calls = tool_calls_from_openai(message.get("tool_calls") or [])

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
                if line in {"[DONE]", "data: [DONE]"}:
                    break
                delta = parse_stream_line(line)
                if delta is not None:
                    yield delta

    def count_tokens(self, messages: list[Message]) -> int:
        """Return the approximate token count for ``messages``.

        Uses ``tiktoken`` for ``gpt-4`` when available; otherwise falls back
        to a rough character-based heuristic.
        """
        text = messages_text(messages)

        try:
            enc = tiktoken.encoding_for_model("gpt-4")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4
