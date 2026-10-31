"""Tests for diff-bounded Python hypothesis generation."""

from __future__ import annotations

from pathlib import Path

from enten.llm.client import AgentLoopSpec
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.review.diff import DiffIndex
from enten.review.hypothesis import SUBMIT_HYPOTHESES_SCHEMA, LLMHypothesisGenerator
from enten.review.run import Proposal

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""

SOURCE = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"


class FakeClient:
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
        _ = tools, run_id, model
        self.messages = messages
        await agent_loop.execute(
            ToolUse(
                id="tool-1",
                name=SUBMIT_HYPOTHESES_SCHEMA.name,
                arguments={"hypotheses": self.findings},
            )
        )
        return LLMResponse(
            content="reviewed",
            tool_calls=[],
            usage=Usage(input_tokens=100, output_tokens=20),
            model="deepseek/deepseek-v4.1-flash-1017",
            raw={},
        )


def _proposal(tmp_path: Path) -> Proposal:
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return Proposal(
        repository_path=tmp_path,
        files=("app.py",),
        verification_command=("true",),
        diff=DIFF,
        changed=DiffIndex.from_patch(DIFF),
    )


async def test_hypothesis_generator_accepts_a_targeted_probe(tmp_path: Path) -> None:
    client = FakeClient(
        [
            {
                "category": "bug",
                "severity": "high",
                "message": "The helper subtracts instead of adding.",
                "file": "app.py",
                "line": 2,
                "rationale": "The changed line is arithmetic.",
                "test_source": SOURCE,
            }
        ]
    )

    result = await LLMHypothesisGenerator(client).generate(_proposal(tmp_path))

    assert len(result.hypotheses) == 1
    assert result.hypotheses[0].file == "app.py"
    assert result.hypotheses[0].line == 2
    assert result.tokens_in == 100
    assert result.tokens_out == 20
    user_message = next(
        message
        for message in client.messages
        if isinstance(message, Message) and message.role == "user"
    )
    assert isinstance(user_message.content, str)
    assert "return a - b" in user_message.content


async def test_hypothesis_generator_drops_findings_outside_the_diff(tmp_path: Path) -> None:
    client = FakeClient(
        [
            {
                "category": "bug",
                "severity": "high",
                "message": "Unchanged line.",
                "file": "app.py",
                "line": 1,
                "test_source": SOURCE,
            }
        ]
    )

    result = await LLMHypothesisGenerator(client).generate(_proposal(tmp_path))

    assert result.hypotheses == ()


async def test_hypothesis_generator_returns_empty_without_a_diff(tmp_path: Path) -> None:
    proposal = Proposal(repository_path=tmp_path, files=("app.py",), verification_command=("true",))
    result = await LLMHypothesisGenerator(FakeClient([])).generate(proposal)
    assert result.hypotheses == ()


async def test_hypothesis_generator_allows_an_empty_submission(tmp_path: Path) -> None:
    result = await LLMHypothesisGenerator(FakeClient([])).generate(_proposal(tmp_path))
    assert result.hypotheses == ()
