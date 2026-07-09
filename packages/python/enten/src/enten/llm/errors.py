"""Domain exceptions raised by LLM providers.

``LLMError`` is the shared base class. Concrete providers raise the
specific subclasses so callers can distinguish authentication failures,
rate limits, and backend unavailability without importing every provider
module.
"""

from __future__ import annotations

from enten.llm.types import LLMUnavailableError as _LLMUnavailableError


class LLMError(Exception):
    """Base class for all LLM provider errors."""


class LLMAuthenticationError(LLMError):
    """Raised when the provider rejects the configured API key."""


class LLMRateLimitError(LLMError):
    """Raised when the provider reports a rate limit."""


class LLMUnavailableError(LLMError, _LLMUnavailableError):
    """Raised when the configured LLM backend is unreachable or missing a model."""
