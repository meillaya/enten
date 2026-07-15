"""RAG-augmented review generator with structured tool output.

The generator assembles a prompt from the PR diff, PR metadata, and
retrieved code chunks, then asks an LLM to submit findings via the
``submit_review`` tool.  Every call is tracked by the supplied
``llm_client`` (T27).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from enten.llm.client import AgentLoopSpec
from enten.llm.injection import wrap_untrusted
from enten.llm.prompts import load_prompt, render
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse


class FindingSummary(BaseModel):
    """A single structured finding returned by the review agent."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    category: str
    severity: str
    message: str
    file: str = Field(min_length=1)
    line: int = Field(gt=0)


class ReviewDraft(BaseModel):
    """Structured output of the review generation step."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    findings: list[FindingSummary]
    summary_text: str
    run_id: str


class _LLMClient(Protocol):
    """Protocol for the LLM client used by :func:`generate_review`."""

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, object]],
        tools: list[ToolSchema | dict[str, object]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse: ...


_FINDING_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "severity": {"type": "string"},
        "message": {"type": "string"},
        "file": {"type": "string"},
        "line": {"type": "integer"},
    },
    "required": ["category", "severity", "message", "file", "line"],
}

SUBMIT_REVIEW_SCHEMA = ToolSchema(
    name="submit_review",
    description="Submit the structured code-review findings.",
    parameters={
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": _FINDING_SCHEMA,
            },
        },
        "required": ["findings"],
    },
)


def _pr_data(pr: Mapping[str, object] | object) -> dict[str, object]:
    """Normalize ``pr`` to a plain dictionary."""
    if isinstance(pr, Mapping):
        mapping = cast(Mapping[str, object], pr)
        return {k: v for k, v in mapping.items()}
    if isinstance(pr, BaseModel):
        return pr.model_dump()
    return {
        "number": getattr(pr, "number", None),
        "title": getattr(pr, "title", None),
        "body": getattr(pr, "body", None),
        "diff": getattr(pr, "diff", None),
    }


def _finding_exists(finding: FindingSummary, root: Path) -> bool:
    try:
        candidate = (root / finding.file).resolve(strict=True)
        candidate.relative_to(root)
        if not candidate.is_file():
            return False
        with candidate.open(encoding="utf-8") as source:
            return any(number == finding.line for number, _line in enumerate(source, start=1))
    except (OSError, UnicodeDecodeError, ValueError):
        return False


async def generate_review(
    pr: Mapping[str, object] | object,
    retrieved_chunks: list[str],
    sandbox_path: str,
    llm_client: _LLMClient,
) -> ReviewDraft:
    """Generate a structured review draft for ``pr``.

    Args:
        pr: Pull-request-like object with ``number``, ``title``, ``body``,
            and ``diff`` keys/attributes.
        retrieved_chunks: Top-K retrieved code/text chunks.
        sandbox_path: Filesystem path to the checked-out PR sandbox.
        llm_client: An object implementing ``complete_with_tools`` (T27).

    Returns:
        A :class:`ReviewDraft` populated from the LLM's tool call.
    """
    source_root = Path(sandbox_path).resolve(strict=True)
    data = _pr_data(pr)
    number = int(cast(int, data["number"]))
    title = str(data.get("title", ""))
    body = str(data.get("body") or "")
    diff = str(data.get("diff", ""))

    review_prompt = load_prompt("review", version=1)
    base_prompt = load_prompt("system_base", version=1)

    system = f"{base_prompt.system.strip()}\n\n{review_prompt.system.strip()}"
    variables = {
        "diff": wrap_untrusted(diff, "diff"),
        "pr_title": wrap_untrusted(title, "pr_description"),
        "pr_body": wrap_untrusted(body, "pr_description"),
        "retrieved_context": "\n\n".join(
            wrap_untrusted(chunk, "code") for chunk in retrieved_chunks
        ),
    }
    _review_system, user = render(review_prompt, variables)

    findings: list[FindingSummary] = []

    async def _execute(tool: ToolUse) -> str:
        if tool.name != SUBMIT_REVIEW_SCHEMA.name:
            return f"unknown tool: {tool.name}"
        submitted = cast(list[object], tool.arguments.get("findings", []))
        for raw in submitted:
            finding = FindingSummary.model_validate(raw)
            if _finding_exists(finding, source_root):
                findings.append(finding)
        return "submitted"

    agent_loop = AgentLoopSpec(
        tools=[SUBMIT_REVIEW_SCHEMA],
        execute=_execute,
    )

    run_id = f"review-{number}"
    response = await llm_client.complete_with_tools(
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
        ],
        tools=[SUBMIT_REVIEW_SCHEMA],
        agent_loop=agent_loop,
        run_id=run_id,
    )

    return ReviewDraft(
        findings=findings,
        summary_text=response.content or "",
        run_id=run_id,
    )


__all__ = [
    "FindingSummary",
    "ReviewDraft",
    "SUBMIT_REVIEW_SCHEMA",
    "generate_review",
]
