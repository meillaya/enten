"""Stub LLM provider for tests and local development.

``StubProvider`` is a concrete, dependency-free implementation of
``LLMProvider`` that returns deterministic responses. It is useful for
unit tests that need an ``isinstance(provider, LLMProvider)`` object
without configuring API keys or network access.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from enten.llm.types import (
    LLMDelta,
    LLMResponse,
    Message,
    ToolSchema,
    Usage,
)


class StubProvider:
    """Concrete ``LLMProvider`` that returns constant responses."""

    def __init__(
        self,
        content: str = "Hello from StubProvider",
        model: str = "stub-model",
        usage: Usage | None = None,
    ) -> None:
        self.content = content
        self.model = model
        self.usage = usage or Usage(input_tokens=0, output_tokens=0)

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> LLMResponse:
        """Return a constant ``LLMResponse``."""
        return LLMResponse(
            content=self.content,
            tool_calls=[],
            usage=self.usage,
            model=self.model,
            raw={"provider": "stub"},
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        """Yield a single delta containing the constant content."""
        yield LLMDelta(content=self.content)

    def count_tokens(self, messages: list[Message]) -> int:
        """Return a rough, deterministic token estimate.

        The heuristic (two tokens per whitespace-separated word) is good
        enough for tests and avoids pulling in tokenizer dependencies in
        the stub. ``"hi there"`` returns ``4``.
        """
        total = 0
        for message in messages:
            content = message.content
            if isinstance(content, str):
                total += len(content.split()) * 2
            else:
                for part in content:
                    if part.text:
                        total += len(part.text.split()) * 2
        return max(total, 1)
