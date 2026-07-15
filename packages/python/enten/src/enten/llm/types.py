"""Pydantic schemas and provider protocol for LLM interactions.

This module defines the canonical data shapes passed to and returned from
any LLM provider implementation in ``enten``. All schemas are pydantic v2
``BaseModel`` subclasses so callers can rely on ``model_validate`` /
``model_dump`` everywhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolUse(BaseModel):
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    """A single message in an LLM conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart]
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolUse] = Field(default_factory=list)


class ContentPart(BaseModel):
    """A multimodal content part (text or image)."""

    type: Literal["text", "image"]
    text: str | None = None
    image_url: str | None = None


class ToolSchema(BaseModel):
    """JSON-schema description of a tool exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    """Token usage for a single LLM request."""

    input_tokens: int
    output_tokens: int


class LLMResponse(BaseModel):
    """Normalized response from any LLM provider."""

    content: str
    tool_calls: list[ToolUse]
    usage: Usage
    model: str
    raw: dict


class LLMDelta(BaseModel):
    """Incremental chunk emitted by a streaming LLM response."""

    content: str = ""
    tool_calls: list[ToolUse] | None = None


class TokenBudgetError(Exception):
    """Raised when a request exceeds the configured token budget."""


class ToolSchemaParseError(Exception):
    """Raised when a provider response cannot be parsed as tool calls."""


class LLMUnavailableError(Exception):
    """Raised when the configured LLM backend is unreachable or missing a model."""


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol implemented by every concrete LLM backend.

    The interface is intentionally provider-agnostic: no single vendor's
    response shape leaks into the method signatures. Implementations are
    responsible for translating between vendor APIs and the normalized
    ``LLMResponse`` / ``LLMDelta`` models.
    """

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> LLMResponse:
        """Issue a non-streaming completion request."""
        ...

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        """Issue a streaming completion request."""
        ...

    def count_tokens(self, messages: list[Message]) -> int:
        """Return the approximate token count for ``messages``."""
        ...
