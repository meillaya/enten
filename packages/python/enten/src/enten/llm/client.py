"""LLM client wrapper with cost tracking, per-PR cap, and loop breaker."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enten.config import LLMProvider as ConfigLLMProvider
from enten.config import Settings
from enten.db import get_session
from enten.llm.client_tools import (
    UnknownToolError as UnknownToolError,
)
from enten.llm.client_tools import (
    compute_cost,
    count_message_tokens,
    tool_hash,
    validate_tool_call,
)
from enten.llm.types import LLMProvider, LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.rate_limit import _default_registry

MAX_COMPLETION_TOKENS: Final = 4096


class AgentLoopSpec(BaseModel):
    """Specification for a tool-use loop driven by :class:`LLMClient`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tools: list[ToolSchema]
    execute: Callable[[ToolUse], Awaitable[str]]


class LoopDetectedError(RuntimeError):
    """Raised when the model repeats the same tool call too many times."""


class CostCapExceeded(RuntimeError):
    """Raised when the per-PR cost cap would be exceeded."""


class LLMClient:
    """Wraps an :class:`~enten.llm.types.LLMProvider` with bookkeeping.

    The client records every call to ``llm_calls``, accrues spend on
    ``pr_reviews.cost_usd`` for the supplied ``run_id``, and enforces a
    hard per-PR cost cap plus a circuit breaker on repetitive tool loops.
    """

    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings,
        db: AsyncSession | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self._db = db
        self._rate_limit_bucket = self._select_rate_limit_bucket(settings)

    @staticmethod
    def _select_rate_limit_bucket(settings: Settings):
        """Return the shared token bucket for the configured LLM provider."""
        if settings.llm_provider == ConfigLLMProvider.OLLAMA:
            return _default_registry.get("ollama", capacity=10, refill_rate=5.0)
        if settings.llm_provider == ConfigLLMProvider.ANTHROPIC:
            return _default_registry.get("anthropic", capacity=60, refill_rate=1.0)
        if "openrouter" in settings.llm_openai_base_url.lower():
            return _default_registry.get("openrouter", capacity=60, refill_rate=1.0)
        return _default_registry.get("openai", capacity=120, refill_rate=2.0)

    async def complete(
        self,
        messages: Sequence[Message | dict[str, Any]],
        tools: Sequence[ToolSchema | dict[str, Any]] | None = None,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        """Issue one completion and persist cost metrics."""
        if not messages:
            raise ValueError("messages must not be empty")

        resolved_model = model or self.settings.llm_default_model
        normalized_messages = [self._as_message(m) for m in messages]
        normalized_tools = [self._as_tool_schema(t) for t in (tools or [])]

        await self._enforce_request_budget(
            run_id,
            resolved_model,
            normalized_messages,
        )

        await asyncio.to_thread(
            self._rate_limit_bucket.acquire,
            blocking=True,
            timeout=30.0,
        )

        started_at = time.monotonic()
        response = await self.provider.complete(
            normalized_messages,
            tools=normalized_tools or None,
            model=resolved_model,
            temperature=0.0,
            max_tokens=MAX_COMPLETION_TOKENS,
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)

        usage = response.usage or Usage(input_tokens=0, output_tokens=0)
        tokens_in = usage.input_tokens
        tokens_out = usage.output_tokens
        cost_usd = compute_cost(resolved_model, tokens_in, tokens_out)

        await self._record_call(
            run_id=run_id,
            model=resolved_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            tool_use_count=len(response.tool_calls),
        )
        return response

    async def complete_with_tools(
        self,
        messages: Sequence[Message | dict[str, Any]],
        tools: Sequence[ToolSchema | dict[str, Any]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        """Run a tool-use loop with a circuit breaker and cost cap."""
        if not messages:
            raise ValueError("messages must not be empty")

        resolved_model = model or self.settings.llm_default_model
        conversation = [self._as_message(m) for m in messages]
        tool_schemas = {t.name: t for t in agent_loop.tools}

        last_hash: str | None = None
        repeat_count = 0

        for _ in range(self.settings.llm_max_iterations):
            response = await self.complete(
                conversation,
                tools=tools,
                run_id=run_id,
                model=resolved_model,
            )

            if not response.tool_calls:
                return response

            conversation.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )

            for tool in response.tool_calls:
                validate_tool_call(tool, tool_schemas)

                current_tool_hash = tool_hash(tool)
                if current_tool_hash == last_hash:
                    repeat_count += 1
                else:
                    last_hash = current_tool_hash
                    repeat_count = 0

                if repeat_count >= 3:
                    raise LoopDetectedError(
                        f"tool loop detected: {tool.name} called "
                        f"with the same arguments {repeat_count} times in a row"
                    )

                result = await agent_loop.execute(tool)
                conversation.append(
                    Message(
                        role="tool",
                        content=str(result),
                        tool_call_id=tool.id,
                    )
                )

        raise RuntimeError(
            f"tool-use loop exceeded max iterations ({self.settings.llm_max_iterations})"
        )

    @staticmethod
    def _as_message(message: Message | dict[str, Any]) -> Message:
        if isinstance(message, Message):
            return message
        if isinstance(message, dict):
            return Message.model_validate(message)
        raise TypeError(f"expected Message or dict, got {type(message)}")

    @staticmethod
    def _as_tool_schema(tool: ToolSchema | dict[str, Any]) -> ToolSchema:
        if isinstance(tool, ToolSchema):
            return tool
        if isinstance(tool, dict):
            return ToolSchema.model_validate(tool)
        raise TypeError(f"expected ToolSchema or dict, got {type(tool)}")

    async def _enforce_request_budget(
        self,
        run_id: str,
        model: str,
        messages: list[Message],
    ) -> None:
        cap = Decimal(str(self.settings.per_pr_cost_cap_usd))
        total = await self._current_cost(run_id)
        projected = compute_cost(
            model,
            count_message_tokens(messages, model),
            MAX_COMPLETION_TOKENS,
        )
        if total + projected > cap:
            raise CostCapExceeded(
                "per-PR cost cap would be exceeded: "
                f"{total} + {projected} > {cap} for run_id={run_id}"
            )

    async def _current_cost(self, run_id: str) -> Decimal:
        from enten.queue.models import LLMCall, PRReview

        async with self._session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(
                    LLMCall.run_id == run_id
                )
            )
            row = Decimal(str(result.scalar_one()))
            if row > 0:
                return row
            # Compatibility for runs recorded before llm_calls.run_id existed.
            legacy = await session.execute(
                select(PRReview.cost_usd).where(PRReview.run_id == run_id)
            )
            return Decimal(str(legacy.scalar_one_or_none() or 0))

    async def _record_call(
        self,
        *,
        run_id: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: Decimal,
        duration_ms: int,
        tool_use_count: int,
    ) -> None:
        from enten.queue.models import LLMCall, PRReview

        async with self._session() as session:
            review_result = await session.execute(select(PRReview).where(PRReview.run_id == run_id))
            review = review_result.scalar_one_or_none()

            if review is not None:
                review.cost_usd = Decimal(review.cost_usd) + cost_usd
                review.tokens_in += tokens_in
                review.tokens_out += tokens_out
            else:
                review = None

            call = LLMCall(
                review_id=review.id if review is not None else None,
                run_id=run_id,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                tool_use_count=tool_use_count,
            )
            session.add(call)
            await session.commit()

    def _session(self):
        if self._db is not None:
            return _ExistingSession(self._db)
        return get_session()


class _ExistingSession:
    """Async-context wrapper that does not close a caller-managed session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass
