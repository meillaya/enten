"""Tests for :mod:`enten.patch`."""

from __future__ import annotations

import hashlib
import inspect
import typing
from typing import Any

import pytest

from enten.llm.client import AgentLoopSpec
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse, Usage
from enten.patch import (
    Patch,
    PatchInvalidError,
    PatchSizeError,
    apply_patch,
    check_patch,
    generate_patch,
)
from enten.sandbox.models import ExecResult, SandboxHandle
from enten.test_gen import ReviewDraft

VALID_DIFF = """\
--- a/file.py
+++ b/file.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


class FakeLLMClient:
    """Stub LLM client that returns a fixed sequence of tool calls."""

    tool_calls: list[ToolUse]
    calls: list[tuple[list[Message | dict[str, Any]], list[ToolSchema | dict[str, Any]], str]]

    def __init__(self, tool_calls: list[ToolUse]) -> None:
        self.tool_calls = tool_calls
        self.calls = []

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[ToolSchema | dict[str, Any]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse:
        _ = model
        self.calls.append((messages, tools, run_id))
        for tool in self.tool_calls:
            _ = await agent_loop.execute(tool)
        return LLMResponse(
            content="done",
            tool_calls=[],
            usage=Usage(input_tokens=0, output_tokens=0),
            model="fake",
            raw={},
        )


class FakeProvider:
    """In-memory sandbox provider for patch tests."""

    results: dict[str, ExecResult]
    calls: list[list[str]]
    uploaded: dict[str, str | bytes]

    def __init__(self, results: dict[str, ExecResult] | None = None) -> None:
        self.results = results or {}
        self.calls = []
        self.uploaded = {}

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        return SandboxHandle(id="fake", provider="fake", image="fake")

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        _ = handle
        _ = timeout_seconds
        self.calls.append(cmd)
        key = " ".join(cmd)
        return self.results.get(
            key,
            ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0),
        )

    async def upload(
        self,
        handle: SandboxHandle,
        files: dict[str, str | bytes],
    ) -> None:
        _ = handle
        self.uploaded.update(files)

    async def destroy(self, handle: SandboxHandle) -> None:
        _ = handle


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """Inject a FakeProvider into :mod:`enten.patch`."""
    provider = FakeProvider()
    monkeypatch.setattr("enten.patch._provider_for", lambda _handle: provider)
    return provider


def _tool(unified_diff: str, rationale: str = "fixes behavior") -> ToolUse:
    return ToolUse(
        id="tc-1",
        name="submit_patch",
        arguments={"unified_diff": unified_diff, "rationale": rationale},
    )


@pytest.mark.asyncio
async def test_generate_and_check_valid_diff(fake_provider: FakeProvider) -> None:
    """A valid diff passes ``git apply --check`` in the sandbox."""
    fake_provider.results = {
        "git apply --check patch.diff": ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0)
    }
    llm_client = FakeLLMClient([_tool(VALID_DIFF, "fix subtraction")])
    sandbox = SandboxHandle(id="fake", provider="docker", image="python:3.12-slim")

    patch = await generate_patch(ReviewDraft(content="original diff"), sandbox, llm_client)

    assert isinstance(patch, Patch)
    assert patch.unified_diff == VALID_DIFF
    assert patch.sha256 == hashlib.sha256(VALID_DIFF.encode("utf-8")).hexdigest()
    assert patch.rationale == "fix subtraction"
    assert patch.applied_in == "sandbox"

    await check_patch(sandbox, patch.unified_diff)

    assert any("git apply --check" in " ".join(c) for c in fake_provider.calls)
    assert "patch.diff" in fake_provider.uploaded


@pytest.mark.asyncio
async def test_invalid_diff_raises_patch_invalid(fake_provider: FakeProvider) -> None:
    """A diff that fails ``git apply --check`` raises PatchInvalidError."""
    fake_provider.results = {
        "git apply --check patch.diff": ExecResult(
            exit_code=1, stdout="", stderr="patch does not apply", duration_ms=0
        )
    }
    llm_client = FakeLLMClient([_tool(VALID_DIFF)])
    sandbox = SandboxHandle(id="fake", provider="docker", image="python:3.12-slim")

    patch = await generate_patch(ReviewDraft(content="original diff"), sandbox, llm_client)

    with pytest.raises(PatchInvalidError, match="git apply --check failed"):
        await check_patch(sandbox, patch.unified_diff)


@pytest.mark.asyncio
async def test_oversized_diff_raises_patch_size_error() -> None:
    """A diff larger than MAX_PATCH_BYTES raises PatchSizeError."""
    huge_diff = "--- a/file.py\n+++ b/file.py\n" + "x\n" * 60_000
    llm_client = FakeLLMClient([_tool(huge_diff, "big change")])
    sandbox = SandboxHandle(id="fake", provider="docker", image="python:3.12-slim")

    with pytest.raises(PatchSizeError, match="exceeds"):
        _ = await generate_patch(ReviewDraft(content="original diff"), sandbox, llm_client)


def test_apply_patch_signature_uses_sandbox_handle() -> None:
    """apply_patch accepts only a SandboxHandle plus the diff; no repo params."""
    sig = inspect.signature(apply_patch)
    assert list(sig.parameters) == ["sandbox", "unified_diff"]

    hints = typing.get_type_hints(apply_patch)
    assert hints["sandbox"] is SandboxHandle

    for name in ("owner", "repo", "branch"):
        assert name not in sig.parameters
