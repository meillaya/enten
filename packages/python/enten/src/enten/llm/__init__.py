"""LLM provider interface, schemas, implementations, and client."""

from __future__ import annotations

from enten.llm.client import AgentLoopSpec, CostCapExceeded, LLMClient, LoopDetectedError
from enten.llm.injection import (
    Finding,
    Kinds,
    ValidationResult,
    validate_review_output,
    wrap_untrusted,
)
from enten.llm.models_pricing import PRICING
from enten.llm.ollama import OllamaProvider
from enten.llm.openai_compat import OpenAICompatProvider
from enten.llm.stub import StubProvider
from enten.llm.types import (
    ContentPart,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    Message,
    TokenBudgetError,
    ToolSchema,
    ToolSchemaParseError,
    ToolUse,
    Usage,
)

__all__ = [
    "AgentLoopSpec",
    "ContentPart",
    "CostCapExceeded",
    "Finding",
    "Kinds",
    "LLMClient",
    "LLMProvider",
    "LLMResponse",
    "LLMUnavailableError",
    "LoopDetectedError",
    "Message",
    "OllamaProvider",
    "OpenAICompatProvider",
    "PRICING",
    "StubProvider",
    "ToolSchema",
    "ToolSchemaParseError",
    "ToolUse",
    "TokenBudgetError",
    "Usage",
    "ValidationResult",
    "validate_review_output",
    "wrap_untrusted",
]
