"""Tests for :mod:`enten.review.generator`."""

from __future__ import annotations

import pytest

from enten.llm.client import AgentLoopSpec, CostCapExceeded
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.review.generator import (
    FindingSummary,
    ReviewDraft,
    generate_review,
)


class FakeLLMClient:
    """Stub LLM client that records calls and executes a fixed tool sequence."""

    tool_calls: list[ToolUse]
    exception: Exception | None
    calls: list[tuple[list[Message | dict[str, object]], list[ToolSchema | dict[str, object]], str]]

    def __init__(
        self,
        tool_calls: list[ToolUse],
        *,
        exception: Exception | None = None,
    ) -> None:
        self.tool_calls = tool_calls
        self.exception = exception
        self.calls = []

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, object]],
        tools: list[ToolSchema | dict[str, object]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        _ = model
        if self.exception is not None:
            raise self.exception
        self.calls.append((messages, tools, run_id))
        for tool in self.tool_calls:
            _ = await agent_loop.execute(tool)
        return LLMResponse(
            content="Review complete.",
            tool_calls=[],
            usage=Usage(input_tokens=0, output_tokens=0),
            model="fake",
            raw={},
        )


def _sample_pr(diff: str = "diff text") -> dict[str, object]:
    return {
        "number": 1,
        "title": "Add feature",
        "body": "This is the PR description.",
        "diff": diff,
    }


def _submit_review_tool(findings: list[dict[str, object]]) -> ToolUse:
    return ToolUse(
        id="tc-1",
        name="submit_review",
        arguments={"findings": findings},
    )


@pytest.mark.asyncio
async def test_generate_review_populates_review_draft() -> None:
    """A synthetic PR + retrieved chunks yields a populated ReviewDraft."""
    finding: dict[str, object] = {
        "category": "bug",
        "severity": "high",
        "message": "Possible null dereference",
        "file": "src/core.py",
        "line": 42,
    }
    client = FakeLLMClient([_submit_review_tool([finding])])

    draft = await generate_review(
        _sample_pr(),
        retrieved_chunks=["def compute(x):\n    return x.value"],
        sandbox_path="/tmp/sandbox",
        llm_client=client,
    )

    assert isinstance(draft, ReviewDraft)
    assert draft.run_id == "review-1"
    assert draft.summary_text == "Review complete."
    assert len(draft.findings) >= 1
    summary = draft.findings[0]
    assert isinstance(summary, FindingSummary)
    assert summary.file == "src/core.py"
    assert summary.line == 42


@pytest.mark.asyncio
async def test_user_message_wraps_untrusted_content() -> None:
    """The user prompt wraps the diff, title, body, and chunks in untrusted tags."""
    finding: dict[str, object] = {
        "category": "style",
        "severity": "low",
        "message": "Missing docstring",
        "file": "src/core.py",
        "line": 1,
    }
    client = FakeLLMClient([_submit_review_tool([finding])])

    _ = await generate_review(
        _sample_pr(diff="+ def foo():\n+     pass"),
        retrieved_chunks=["chunk-a"],
        sandbox_path="/tmp/sandbox",
        llm_client=client,
    )

    assert len(client.calls) == 1
    messages, _tools, run_id = client.calls[0]
    assert run_id == "review-1"
    user_message = next(m for m in messages if isinstance(m, Message) and m.role == "user")
    user_text = user_message.content
    assert isinstance(user_text, str)
    assert "<untrusted-" in user_text
    assert 'kind="diff"' in user_text
    assert "+ def foo():" in user_text
    assert 'kind="pr_description"' in user_text
    assert "Add feature" in user_text
    assert "This is the PR description." in user_text
    assert 'kind="code"' in user_text
    assert "chunk-a" in user_text


@pytest.mark.asyncio
async def test_cost_cap_exceeded_bubbles_up() -> None:
    """When the LLM client hits the per-PR cost cap, the exception propagates."""
    client = FakeLLMClient([], exception=CostCapExceeded("per-PR cost cap reached"))

    with pytest.raises(CostCapExceeded):
        _ = await generate_review(
            _sample_pr(),
            retrieved_chunks=[],
            sandbox_path="/tmp/sandbox",
            llm_client=client,
        )


@pytest.mark.asyncio
async def test_unknown_tool_is_ignored() -> None:
    """A non-submit_review tool call is rejected by the agent loop executor."""
    finding: dict[str, object] = {
        "category": "bug",
        "severity": "medium",
        "message": "Off-by-one",
        "file": "src/loop.py",
        "line": 7,
    }
    client = FakeLLMClient(
        [
            ToolUse(id="tc-bad", name="bad_tool", arguments={}),
            _submit_review_tool([finding]),
        ]
    )

    draft = await generate_review(
        _sample_pr(),
        retrieved_chunks=[],
        sandbox_path="/tmp/sandbox",
        llm_client=client,
    )

    assert len(draft.findings) == 1
    assert draft.findings[0].file == "src/loop.py"
