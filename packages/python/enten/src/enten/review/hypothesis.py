"""Diff-bounded Python hypothesis generation with executable probes."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enten.llm.client import AgentLoopSpec
from enten.llm.client_tools import compute_cost
from enten.llm.injection import wrap_untrusted
from enten.llm.prompts import load_prompt, render
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse
from enten.review.python_probes import validate_probe_source
from enten.review.run import Hypothesis, HypothesisSet, Proposal

MAX_CONTEXT_BYTES = 24_000
_MAX_CONTEXT_FILES = 8
_CONTEXT_LINES = 8


class _Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=100)
    severity: str
    message: str = Field(min_length=1, max_length=1_000)
    file: str = Field(min_length=1, max_length=500)
    line: int = Field(gt=0)
    rationale: str = Field(default="", max_length=2_000)
    test_source: str = Field(min_length=1, max_length=20_000)


SUBMIT_HYPOTHESES_SCHEMA = ToolSchema(
    name="submit_hypotheses",
    description="Submit up to three Python hypotheses, each with a failing pytest probe.",
    parameters={
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "severity": {"type": "string"},
                        "message": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "rationale": {"type": "string"},
                        "test_source": {"type": "string"},
                    },
                    "required": [
                        "category",
                        "severity",
                        "message",
                        "file",
                        "line",
                        "test_source",
                    ],
                },
            }
        },
        "required": ["hypotheses"],
    },
)


class LLMHypothesisGenerator:
    """Generate Python hypotheses and probes from a bounded diff."""

    def __init__(self, llm_client: Any, max_hypotheses: int = 3) -> None:
        self._client = llm_client
        self._max_hypotheses = max(0, min(max_hypotheses, 3))

    async def generate(self, proposal: Proposal) -> HypothesisSet:
        changed = proposal.changed.changed_files(frozenset({".py"}))
        if not changed or not proposal.diff.strip():
            return HypothesisSet(hypotheses=())

        prompt = load_prompt("hypothesis", version=1)
        base_prompt = load_prompt("system_base", version=1)
        system = f"{base_prompt.system.strip()}\n\n{prompt.system.strip()}"
        context = _repository_context(proposal, changed)
        _review_system, user = render(
            prompt,
            {
                "diff": wrap_untrusted(proposal.diff, "diff"),
                "changed_files": wrap_untrusted(
                    "\n".join(f"{path}: lines {_lines(proposal, path)}" for path in changed),
                    "diff",
                ),
                "repository_context": wrap_untrusted(context, "code"),
            },
        )

        accepted: list[Hypothesis] = []

        async def _execute(tool: ToolUse) -> str:
            if tool.name != SUBMIT_HYPOTHESES_SCHEMA.name:
                return f"unknown tool: {tool.name}"
            raw = tool.arguments.get("hypotheses", [])
            if not isinstance(raw, list):
                return "hypotheses must be an array"
            for index, item in enumerate(raw):
                if len(accepted) == self._max_hypotheses:
                    break
                try:
                    submission = _Submission.model_validate(item)
                except Exception:
                    continue
                path = submission.file.removeprefix("a/").removeprefix("b/")
                if not proposal.changed.contains_added(path, submission.line):
                    continue
                if path not in changed:
                    continue
                rejection = validate_probe_source(submission.test_source, path)
                if rejection is not None:
                    continue
                digest = hashlib.sha256(submission.test_source.encode("utf-8")).hexdigest()
                try:
                    accepted.append(
                        Hypothesis(
                            id=f"hyp-{index}-{digest[:12]}",
                            category=submission.category,
                            severity=submission.severity,
                            message=submission.message,
                            file=path,
                            line=submission.line,
                            rationale=submission.rationale,
                            test_source=submission.test_source,
                            test_sha256=digest,
                        )
                    )
                except Exception:
                    continue
            return "submitted"

        response: LLMResponse = await self._client.complete_with_tools(
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            tools=[SUBMIT_HYPOTHESES_SCHEMA],
            agent_loop=AgentLoopSpec(
                tools=[SUBMIT_HYPOTHESES_SCHEMA],
                execute=_execute,
            ),
            run_id=proposal.correlation_run_id or f"hypotheses-{proposal.pr_number}",
        )
        usage = response.usage
        return HypothesisSet(
            hypotheses=tuple(accepted),
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cost_usd=compute_cost(
                response.model,
                usage.input_tokens,
                usage.output_tokens,
            ),
        )


def _lines(proposal: Proposal, path: str) -> str:
    lines = proposal.changed.added_lines.get(path, ())
    return ", ".join(str(line) for line in lines[:20])


def _repository_context(proposal: Proposal, changed: tuple[str, ...]) -> str:
    root = proposal.repository_path.resolve()
    blocks: list[str] = []
    used = 0
    for relative in changed[:_MAX_CONTEXT_FILES]:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text:
            continue
        wanted = _window_lines(text, proposal.changed.added_lines.get(relative, ()))
        numbered = "\n".join(f"{number}: {text.splitlines()[number - 1]}" for number in wanted)
        block = f"File: {relative}\n{numbered}"
        if used + len(block) > MAX_CONTEXT_BYTES:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _window_lines(text: str, added: tuple[int, ...]) -> list[int]:
    total = len(text.splitlines())
    wanted: set[int] = set()
    for line in added[:20]:
        start = max(1, line - _CONTEXT_LINES)
        end = min(total, line + _CONTEXT_LINES)
        wanted.update(range(start, end + 1))
    return sorted(wanted)


__all__ = ["LLMHypothesisGenerator", "SUBMIT_HYPOTHESES_SCHEMA"]
