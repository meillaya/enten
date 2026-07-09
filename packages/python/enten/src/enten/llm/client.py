"""LLM client wrapper with cost tracking, per-PR cap, and loop breaker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import tiktoken
from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enten.config import LLMProvider as ConfigLLMProvider
from enten.config import Settings
from enten.db import get_session
from enten.llm.models_pricing import PRICING
from enten.llm.types import LLMProvider, LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.rate_limit import _default_registry


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

    The client records every call to ``llm_calls`` (T08), accrues spend on
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
        if "openrouter" in settings.llm_openai_base_url.lower():
            return _default_registry.get("anthropic", capacity=60, refill_rate=1.0)
        return _default_registry.get("openai", capacity=120, refill_rate=2.0)

    async def complete(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[ToolSchema | dict[str, Any]] | None = None,
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
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)

        usage = response.usage or Usage(input_tokens=0, output_tokens=0)
        tokens_in = usage.input_tokens
        tokens_out = usage.output_tokens
        cost_usd = self._compute_cost(resolved_model, tokens_in, tokens_out)

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
        messages: list[Message | dict[str, Any]],
        tools: list[ToolSchema | dict[str, Any]],
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
            await self._enforce_cost_cap(run_id)

            response = await self.complete(
                conversation,
                tools=tools,
                run_id=run_id,
                model=resolved_model,
            )

            if not response.tool_calls:
                return response

            conversation.append(
                Message(role="assistant", content=response.content or "")
            )

            for tool in response.tool_calls:
                schema = self._validate_tool(tool, tool_schemas)
                self._validate_arguments(tool, schema)

                tool_hash = self._tool_hash(tool)
                if tool_hash == last_hash:
                    repeat_count += 1
                else:
                    last_hash = tool_hash
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

    @staticmethod
    def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> Decimal:
        prices = PRICING.get(model)
        if prices is None:
            return Decimal("0")
        input_cost = Decimal(tokens_in) * Decimal(str(prices["input"])) / Decimal("1000000")
        output_cost = Decimal(tokens_out) * Decimal(str(prices["output"])) / Decimal("1000000")
        return input_cost + output_cost

    @staticmethod
    def _count_tokens(messages: list[Message], model: str) -> int:
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")

        total = 0
        for message in messages:
            content = message.content
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if part.text:
                        total += len(enc.encode(part.text))
        return total

    @staticmethod
    def _tool_hash(tool: ToolUse) -> str:
        payload = json.dumps(
            {"name": tool.name, "args": tool.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_tool(tool: ToolUse, schemas: dict[str, ToolSchema]) -> ToolSchema:
        if tool.name not in schemas:
            raise ValidationError.from_exception_data(
                title="ToolUse",
                line_errors=[
                    {
                        "loc": ("name",),
                        "msg": f"unknown tool: {tool.name}",
                        "type": "value_error",
                    }
                ],
            )
        return schemas[tool.name]

    @classmethod
    def _validate_arguments(cls, tool: ToolUse, schema: ToolSchema) -> None:
        model_cls = cls._schema_to_model(schema)
        model_cls.model_validate(tool.arguments)

    @classmethod
    def _schema_to_model(cls, schema: ToolSchema) -> type[BaseModel]:
        properties = schema.parameters.get("properties", {})
        required = set(schema.parameters.get("required", []))

        fields: dict[str, Any] = {}
        for name, prop in properties.items():
            field_type = cls._json_schema_type_to_py(prop)
            if name in required:
                fields[name] = (field_type, ...)
            else:
                fields[name] = (field_type | None, None)

        return create_model(
            f"ToolArgs_{schema.name}",
            __config__=ConfigDict(extra="ignore"),
            **fields,
        )

    @classmethod
    def _json_schema_type_to_py(cls, prop: dict[str, Any]) -> type[Any]:
        type_name = prop.get("type", "string")
        if type_name == "string":
            return str
        if type_name == "integer":
            return int
        if type_name == "number":
            return float
        if type_name == "boolean":
            return bool
        if type_name == "array":
            item_type = cls._json_schema_type_to_py(prop.get("items", {}))
            return list[item_type]
        if type_name == "object":
            return dict[str, Any]
        return str

    async def _enforce_cost_cap(self, run_id: str) -> None:
        cap = Decimal(str(self.settings.per_pr_cost_cap_usd))
        total = await self._current_cost(run_id)
        if total >= cap:
            raise CostCapExceeded(
                f"per-PR cost cap reached: {total} >= {cap} for run_id={run_id}"
            )

    async def _current_cost(self, run_id: str) -> Decimal:
        from enten.queue.models import PRReview

        async with self._session() as session:
            result = await session.execute(
                select(PRReview.cost_usd).where(PRReview.run_id == run_id)
            )
            row = result.scalar_one_or_none()
            return row or Decimal("0")

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
            review_result = await session.execute(
                select(PRReview).where(PRReview.run_id == run_id)
            )
            review = review_result.scalar_one_or_none()

            if review is not None:
                review.cost_usd = Decimal(review.cost_usd) + cost_usd
                review.tokens_in += tokens_in
                review.tokens_out += tokens_out
            else:
                review = None

            call = LLMCall(
                review_id=review.id if review is not None else None,
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
