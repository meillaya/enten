"""Ollama-native LLM provider with tool-use support.

Ollama's ``/api/chat`` endpoint accepts the same function-calling ``tools``
shape as OpenAI, but returns tool calls under ``message.tool_calls`` instead
of the top-level ``tool_calls`` field. This provider maps those responses
onto the normalized :class:`~enten.llm.types.LLMResponse` /
:class:`~enten.llm.types.LLMDelta` models.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from enten.config import Settings
from enten.llm.types import (
    LLMDelta,
    LLMResponse,
    LLMUnavailableError,
    Message,
    ToolSchema,
    ToolUse,
    Usage,
)


class OllamaProvider:
    """Native Ollama backend for chat completions and tool use."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_ollama_base_url.rstrip("/")
        self.default_model = settings.llm_default_model
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=60.0)

    def _resolve_model(self, model: str | None) -> str:
        return model or self.default_model

    @staticmethod
    def _message_to_ollama(message: Message) -> dict[str, Any]:
        """Convert a normalized Message into Ollama's message dict."""
        content = message.content
        if isinstance(content, list):
            content = "\n".join(part.text or "" for part in content)

        out: dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_call_id:
            out["tool_call_id"] = message.tool_call_id
        if message.name:
            out["name"] = message.name
        return out

    @staticmethod
    def _tool_to_ollama(tool: ToolSchema) -> dict[str, Any]:
        """Convert a ToolSchema into Ollama's OpenAI-compatible tool dict."""
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
        """Flatten messages into a single string for rough token counting."""
        parts: list[str] = []
        for message in messages:
            content = message.content
            if isinstance(content, list):
                parts.extend(part.text or "" for part in content)
            else:
                parts.append(content)
        return "\n".join(parts)

    def _payload(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        model: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._resolve_model(model),
            "messages": [self._message_to_ollama(m) for m in messages],
            "stream": stream,
        }
        if tools:
            body["tools"] = [self._tool_to_ollama(t) for t in tools]
        return body

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> LLMResponse:
        """Issue a non-streaming chat request to Ollama."""
        model = opts.get("model")
        payload = self._payload(messages, tools, model, stream=False)
        response = await self._client.post("/api/chat", json=payload)

        if response.status_code == 404:
            raise LLMUnavailableError(
                f"Ollama model '{payload['model']}' not found or server unavailable",
            )
        response.raise_for_status()

        data = response.json()
        message = data.get("message") or {}
        content = message.get("content") or ""

        tool_calls: list[ToolUse] = []
        for index, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            tool_calls.append(
                ToolUse(
                    id=tc.get("id") or f"call_{index}",
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments") or {},
                ),
            )

        usage = Usage(
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", payload["model"]),
            raw=data,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        """Issue a streaming chat request to Ollama."""
        model = opts.get("model")
        payload = self._payload(messages, tools, model, stream=True)

        async with self._client.stream("POST", "/api/chat", json=payload) as response:
            if response.status_code == 404:
                raise LLMUnavailableError(
                    f"Ollama model '{payload['model']}' not found or server unavailable",
                )
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line:
                    continue

                # OpenAI-style SSE lines include a ``data: `` prefix;
                # Ollama returns raw NDJSON, but accept both.
                if line.startswith("data: "):
                    line = line[6:]

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = data.get("message") or {}
                delta_content = msg.get("content") or ""

                delta_tool_calls: list[ToolUse] | None = None
                raw_tool_calls = msg.get("tool_calls")
                if raw_tool_calls:
                    delta_tool_calls = []
                    for index, tc in enumerate(raw_tool_calls):
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        delta_tool_calls.append(
                            ToolUse(
                                id=tc.get("id") or f"call_{index}",
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments") or {},
                            ),
                        )

                yield LLMDelta(content=delta_content, tool_calls=delta_tool_calls)

    def count_tokens(self, messages: list[Message]) -> int:
        """Return an approximate token count for ``messages``.

        Ollama does not expose a tokenize endpoint, so we first ask
        ``/api/show`` for model metadata and fall back to a rough
        character-based estimate (one token per four characters).
        """
        text = self._text_from_messages(messages)
        model = self.default_model

        try:
            info_response = httpx.post(
                f"{self.base_url}/api/show",
                json={"name": model},
                timeout=5.0,
            )
            info_response.raise_for_status()
            info = info_response.json()

            # Ollama does not currently return tokenizer details, but if it
            # ever does we should prefer a real count over the heuristic.
            tokenizer = info.get("tokenizer")
            if isinstance(tokenizer, dict):
                tokens = tokenizer.get("tokens")
                if isinstance(tokens, list):
                    return len(tokens)
        except Exception:
            pass

        return len(text) // 4
