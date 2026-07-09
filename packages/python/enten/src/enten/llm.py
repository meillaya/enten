"""LLM provider interface (T06 stub, fleshed out in T24+).

The full ``LLMProvider`` lives in T24. This stub exists so the
``no_llm_calls`` autouse fixture in ``conftest.py`` can monkeypatch
``LLMProvider.complete`` from day one — tests never hit a real
LLM by accident.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMCallWithoutMock(RuntimeError):
    """Raised when a test calls the LLM without explicitly opting in."""


class LLMProvider(ABC):
    """Abstract base for every concrete provider (OpenAI-compat, Ollama)."""

    name: str = "abstract"

    @abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> str:  # pragma: no cover - interface
        raise NotImplementedError
