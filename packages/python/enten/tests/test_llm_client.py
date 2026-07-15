"""Tests for :class:`enten.llm.client.LLMClient`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enten.config import Settings
from enten.db import Base
from enten.llm.client import AgentLoopSpec, CostCapExceeded, LLMClient, LoopDetectedError
from enten.llm.types import LLMDelta, LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.queue.models import Job, LLMCall, PRReview


class FakeProvider:
    """In-memory provider for unit tests."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.index = 0
        self.calls: list[tuple[list[Message], list[ToolSchema] | None, dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> LLMResponse:
        self.calls.append((messages, tools, opts))
        response = self.responses[self.index % len(self.responses)]
        self.index += 1
        return response

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **opts: Any,
    ) -> AsyncIterator[LLMDelta]:
        yield LLMDelta()

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content) for m in messages if isinstance(m.content, str))


@pytest_asyncio.fixture
async def session(db_session: dict[str, Any]) -> Any:
    """Yield a fresh SQLAlchemy async session over a testcontainers database."""
    engine = create_async_engine(db_session["dsn"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture
async def review(session: Any) -> PRReview:
    """Create a Job + PRReview pair linked by a stable run_id."""
    run_id = "run-test-llm-client"
    session.add(
        Job(
            run_id=run_id,
            kind="test",
            state="running",
            payload={},
        )
    )
    review = PRReview(
        run_id=run_id,
        repo_id="owner/repo",
        pr_number=1,
        commit_sha="abc123",
        conclusion="pending",
        cost_usd=Decimal("0"),
    )
    session.add(review)
    await session.commit()
    return review


def _settings(**kwargs: Any) -> Settings:
    return Settings.model_validate(kwargs)


def _run_id(review: PRReview) -> str:
    assert review.run_id is not None
    return review.run_id


@pytest.mark.slow
async def test_cost_accrual_records_spend(session: Any, review: PRReview) -> None:
    """Three 1k-in / 500-out gpt-4o-mini calls sum to the expected cost."""
    provider = FakeProvider(
        [
            LLMResponse(
                content="ok",
                tool_calls=[],
                usage=Usage(input_tokens=1000, output_tokens=500),
                model="gpt-4o-mini",
                raw={},
            ),
        ]
    )
    client = LLMClient(provider, _settings(), db=session)

    for _ in range(3):
        await client.complete(
            [Message(role="user", content="hello")],
            run_id=_run_id(review),
            model="gpt-4o-mini",
        )

    await session.refresh(review)
    # 3 * (1000/1M * 0.15 + 500/1M * 0.60) = 0.00135
    assert float(review.cost_usd) == pytest.approx(0.00135, abs=1e-9)
    assert review.tokens_in == 3000
    assert review.tokens_out == 1500

    calls_result = await session.execute(select(LLMCall).where(LLMCall.review_id == review.id))
    assert len(calls_result.scalars().all()) == 3


@pytest.mark.slow
async def test_loop_detection_breaks_repeated_tool_calls(session: Any, review: PRReview) -> None:
    """Four identical tool calls in a row trip the circuit breaker."""
    tool = ToolUse(id="tc-1", name="repeat", arguments={"x": 1})
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[tool],
                usage=Usage(input_tokens=10, output_tokens=10),
                model="gpt-4o-mini",
                raw={},
            ),
        ]
    )
    schema = ToolSchema(
        name="repeat",
        description="repeat",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
    )

    async def _execute(_tool: ToolUse) -> str:
        return "done"

    agent_loop = AgentLoopSpec(
        tools=[schema],
        execute=_execute,
    )
    client = LLMClient(provider, _settings(), db=session)

    with pytest.raises(LoopDetectedError):
        await client.complete_with_tools(
            [Message(role="user", content="loop")],
            tools=[schema],
            agent_loop=agent_loop,
            run_id=_run_id(review),
            model="gpt-4o-mini",
        )

    # The 4th identical call is detected before its tool is executed.
    assert provider.index == 4

    calls_result = await session.execute(select(LLMCall).where(LLMCall.review_id == review.id))
    assert len(calls_result.scalars().all()) == 4


@pytest.mark.slow
async def test_cost_cap_stops_loop(session: Any, review: PRReview) -> None:
    review.cost_usd = Decimal("0.49")
    await session.commit()

    tool = ToolUse(id="tc-1", name="spend", arguments={"amount": 0})
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[tool],
                usage=Usage(input_tokens=10000, output_tokens=7500),
                model="gpt-4o",
                raw={},
            ),
        ]
    )
    schema = ToolSchema(
        name="spend",
        description="spend",
        parameters={
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
        },
    )

    async def _execute(_tool: ToolUse) -> str:
        return "spent"

    agent_loop = AgentLoopSpec(
        tools=[schema],
        execute=_execute,
    )
    client = LLMClient(provider, _settings(per_pr_cost_cap_usd=0.50), db=session)

    with pytest.raises(CostCapExceeded):
        await client.complete_with_tools(
            [Message(role="user", content="spend")],
            tools=[schema],
            agent_loop=agent_loop,
            run_id=_run_id(review),
            model="gpt-4o",
        )

    assert provider.index == 0

    await session.refresh(review)
    assert float(review.cost_usd) == pytest.approx(0.49, abs=1e-6)


@pytest.mark.slow
async def test_tool_loop_preserves_assistant_tool_calls(
    session: Any,
    review: PRReview,
) -> None:
    tool = ToolUse(id="tc-history", name="lookup", arguments={"path": "a.py"})
    provider = FakeProvider(
        [
            LLMResponse(
                content="checking",
                tool_calls=[tool],
                usage=Usage(input_tokens=10, output_tokens=5),
                model="gpt-4o-mini",
                raw={},
            ),
            LLMResponse(
                content="done",
                tool_calls=[],
                usage=Usage(input_tokens=12, output_tokens=4),
                model="gpt-4o-mini",
                raw={},
            ),
        ]
    )
    schema = ToolSchema(
        name="lookup",
        description="Read a repository file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    async def _execute(_tool: ToolUse) -> str:
        return "contents"

    client = LLMClient(provider, _settings(), db=session)
    response = await client.complete_with_tools(
        [Message(role="user", content="inspect")],
        tools=[schema],
        agent_loop=AgentLoopSpec(tools=[schema], execute=_execute),
        run_id=_run_id(review),
    )

    assert response.content == "done"
    second_messages = provider.calls[1][0]
    assert second_messages[-2].role == "assistant"
    assert second_messages[-2].tool_calls == [tool]
    assert second_messages[-1].tool_call_id == tool.id


@pytest.mark.slow
async def test_llm_call_rows_persisted(session: Any, review: PRReview) -> None:
    """Every completion writes one row to ``llm_calls``."""
    provider = FakeProvider(
        [
            LLMResponse(
                content=f"resp-{i}",
                tool_calls=[],
                usage=Usage(input_tokens=10, output_tokens=5),
                model="gpt-4o-mini",
                raw={},
            )
            for i in range(5)
        ]
    )
    client = LLMClient(provider, _settings(), db=session)

    for i in range(5):
        await client.complete(
            [Message(role="user", content=f"q-{i}")],
            run_id=_run_id(review),
        )

    calls_result = await session.execute(select(LLMCall).where(LLMCall.review_id == review.id))
    calls = calls_result.scalars().all()
    assert len(calls) == 5
    assert {c.model for c in calls} == {"gpt-4o-mini"}
    assert all(c.duration_ms >= 0 for c in calls)
