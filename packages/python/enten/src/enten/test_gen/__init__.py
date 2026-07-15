"""Test generation with sandbox collect-only validation.

Given a review draft and retrieved context, produce pytest or Vitest test
files via an LLM tool call, validate them in a sandbox, and return the
surviving tests as :class:`GeneratedTests`.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from enten.llm.client import AgentLoopSpec
from enten.llm.injection import wrap_untrusted
from enten.llm.prompts import load_prompt, render
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse
from enten.parsers.models import PytestResult, VitestResult
from enten.parsers.runners import run_pytest, run_vitest
from enten.sandbox.files import UnsafeSandboxPathError, write_sandbox_files
from enten.sandbox.protocol import SandboxProvider

MAX_PATCH_BYTES = 50_000


class ReviewDraft(BaseModel):
    """Code under review plus an optional run id for cost tracking."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    content: str
    run_id: str | None = None


class GeneratedTest(BaseModel):
    """A single generated test file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    file_path: str
    content: str
    sha256: str
    rationale: str


class GeneratedTests(BaseModel):
    """Collection of generated tests for a target language."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    language: Literal["python", "typescript"]
    tests: list[GeneratedTest]


class GenerationError(Exception):
    """Raised when generated tests fail collect-only validation."""


class _LLMClient(Protocol):
    """Protocol for the LLM client used by :func:`generate_tests`."""

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[ToolSchema | dict[str, Any]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse: ...


SUBMIT_TESTS_SCHEMA = ToolSchema(
    name="submit_tests",
    description="Submit a generated test file. Call once per file.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["file_path", "content", "rationale"],
    },
)


def _build_code(draft: ReviewDraft, retrieved_chunks: list[str]) -> str:
    parts = [draft.content]
    parts.extend(retrieved_chunks)
    return "\n\n".join(parts)


def _write_repo_files(tests: list[GeneratedTest]) -> str:
    tmpdir = Path(tempfile.mkdtemp(prefix="enten-test-gen-"))
    try:
        write_sandbox_files(
            tmpdir,
            {test.file_path: test.content for test in tests},
        )
    except UnsafeSandboxPathError as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise GenerationError(str(exc)) from exc
    return str(tmpdir)


def _check_size(tests: list[GeneratedTest]) -> None:
    total = sum(len(t.content.encode("utf-8")) for t in tests)
    if total > MAX_PATCH_BYTES:
        raise GenerationError(f"Combined generated tests exceed {MAX_PATCH_BYTES} bytes ({total})")


async def generate_tests(
    review_draft: ReviewDraft | str,
    retrieved_chunks: list[str],
    language: Literal["python", "typescript"],
    llm_client: _LLMClient,
    sandbox: SandboxProvider,
) -> GeneratedTests:
    """Generate tests for ``language`` and validate them in ``sandbox``.

    The LLM is given the ``submit_tests`` tool. Each tool call produces one
    :class:`GeneratedTest`. After collection, the tests are written to a
    temporary directory that is mounted into the sandbox as ``/workspace``
    and validated with ``pytest --collect-only`` (Python) or
    ``vitest --run --reporter=json`` (TypeScript). If validation fails,
    :class:`GenerationError` is raised and no tests are returned.
    """
    draft = ReviewDraft(content=review_draft) if isinstance(review_draft, str) else review_draft

    prompt = load_prompt("test_gen", version=1)
    base_prompt = load_prompt("system_base", version=1)
    code = _build_code(draft, retrieved_chunks)
    _prompt_system, user = render(
        prompt,
        {"code": wrap_untrusted(code, "code"), "language": language},
    )
    system = f"{base_prompt.system.strip()}\n\n{prompt.system.strip()}"

    collected: list[GeneratedTest] = []

    async def _execute(tool: ToolUse) -> str:
        if tool.name != SUBMIT_TESTS_SCHEMA.name:
            return f"unknown tool: {tool.name}"
        args = tool.arguments
        file_path = cast(str, args.get("file_path", ""))
        content = cast(str, args.get("content", ""))
        rationale = cast(str, args.get("rationale", ""))
        test = GeneratedTest(
            file_path=file_path,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            rationale=rationale,
        )
        collected.append(test)
        return f"saved {test.file_path}"

    agent_loop = AgentLoopSpec(
        tools=[SUBMIT_TESTS_SCHEMA],
        execute=_execute,
    )

    run_id = draft.run_id or f"test-gen-{uuid.uuid4().hex}"
    _ = await llm_client.complete_with_tools(
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
        ],
        tools=[SUBMIT_TESTS_SCHEMA],
        agent_loop=agent_loop,
        run_id=run_id,
    )

    if not collected:
        raise GenerationError("LLM did not submit any tests")

    _check_size(collected)

    repo_path = _write_repo_files(collected)
    try:
        if language == "python":
            result: PytestResult | VitestResult = await run_pytest(
                sandbox, repo_path, collect_only=True
            )
        else:
            result = await run_vitest(sandbox, repo_path)
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)

    if result.exit_code != 0:
        raise GenerationError(
            f"Test collection failed for {language} (exit_code={result.exit_code})"
        )

    return GeneratedTests(language=language, tests=collected)


__all__ = [
    "GeneratedTest",
    "GeneratedTests",
    "GenerationError",
    "ReviewDraft",
    "generate_tests",
]
