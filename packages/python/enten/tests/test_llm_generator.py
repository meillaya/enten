"""Tests for the LLM-backed findings generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from enten.llm.client import AgentLoopSpec
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.review.llm import LLMFindingsGenerator
from enten.review.run import Proposal


class FakeLLMClient:
    """Execute the review tool loop and return a fixed structured response."""

    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings
        self.messages: list[Message | dict[str, object]] = []

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, object]],
        tools: list[ToolSchema | dict[str, object]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        _ = tools, model
        self.messages = messages
        tool = ToolUse(
            id="tool-1",
            name="submit_review",
            arguments={"findings": self.findings},
        )
        _ = await agent_loop.execute(tool)
        _ = run_id
        return LLMResponse(
            content="one finding",
            tool_calls=[],
            usage=Usage(input_tokens=100, output_tokens=20),
            model="deepseek/deepseek-v4.1-flash-1017",
            raw={},
        )


def _proposal(root: Path) -> Proposal:
    return Proposal(
        repository_path=root,
        files=("src/app.py",),
        verification_command=("true",),
        pr_number=1,
        correlation_run_id="run-1",
    )


@pytest.mark.asyncio
async def test_llm_generator_returns_grounded_findings(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("def handler(user_input):\n    value = user_input\n    return eval(value)\n")
    client = FakeLLMClient(
        [
            {
                "category": "security.dynamic-evaluation",
                "severity": "high",
                "message": "eval accepts executable input.",
                "file": "src/app.py",
                "line": 3,
            }
        ]
    )

    result = await LLMFindingsGenerator(client).generate(_proposal(tmp_path))

    assert len(result.findings) == 1
    assert result.findings[0].file == "src/app.py"
    assert result.findings[0].line == 3
    assert result.tokens_in == 100
    assert result.tokens_out == 20
    assert result.cost_usd > 0
    user_message = next(
        message
        for message in client.messages
        if isinstance(message, Message) and message.role == "user"
    )
    assert isinstance(user_message.content, str)
    assert "3:     return eval(value)" in user_message.content


@pytest.mark.asyncio
async def test_llm_generator_drops_invalid_severity(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("value = 1\n")
    client = FakeLLMClient(
        [
            {
                "category": "style",
                "severity": "info",
                "message": "Not a supported severity.",
                "file": "app.py",
                "line": 1,
            }
        ]
    )

    result = await LLMFindingsGenerator(client).generate(
        Proposal(repository_path=tmp_path, files=("app.py",), verification_command=("true",))
    )

    assert result.findings == ()
