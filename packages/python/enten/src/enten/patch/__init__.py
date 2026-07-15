"""Patch generator with sandbox-only application.

Produces a unified diff via an LLM tool call, validates it with
``git apply --check``, and applies it inside a sandbox. Patches are never
pushed to a GitHub branch from this module.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel

from enten.config import Settings
from enten.llm.client import AgentLoopSpec
from enten.llm.injection import wrap_untrusted
from enten.llm.prompts import load_prompt, render
from enten.llm.types import LLMResponse, Message, ToolSchema, ToolUse
from enten.sandbox import get_provider
from enten.sandbox.models import SandboxHandle
from enten.sandbox.protocol import SandboxProvider
from enten.test_gen import ReviewDraft

MAX_PATCH_BYTES = 50_000


class Patch(BaseModel):
    """A generated patch ready for sandbox-only application."""

    unified_diff: str
    sha256: str
    rationale: str
    applied_in: Literal["sandbox"] = "sandbox"


class PatchInvalidError(Exception):
    """Raised when a patch fails format or ``git apply --check`` validation."""


class PatchApplyError(Exception):
    """Raised when ``git apply`` fails inside the sandbox."""


class PatchSizeError(Exception):
    """Raised when a patch exceeds ``MAX_PATCH_BYTES``."""


class _LLMClient(Protocol):
    """Protocol for the LLM client used by :func:`generate_patch`."""

    async def complete_with_tools(
        self,
        messages: list[Message | dict[str, Any]],
        tools: list[ToolSchema | dict[str, Any]],
        agent_loop: AgentLoopSpec,
        *,
        run_id: str,
        model: str | None = None,
    ) -> LLMResponse: ...


SUBMIT_PATCH_SCHEMA = ToolSchema(
    name="submit_patch",
    description="Submit a generated patch as a unified diff.",
    parameters={
        "type": "object",
        "properties": {
            "unified_diff": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["unified_diff", "rationale"],
    },
)


def _validate_format(unified_diff: str) -> None:
    """Ensure ``unified_diff`` starts with ``--- a/`` and ``+++ b/``."""
    lines = unified_diff.splitlines()
    if len(lines) < 2 or not lines[0].startswith("--- a/") or not lines[1].startswith("+++ b/"):
        raise PatchInvalidError("unified_diff must start with '--- a/' and '+++ b/'")


def _validate_size(unified_diff: str) -> None:
    """Ensure ``unified_diff`` does not exceed ``MAX_PATCH_BYTES``."""
    size = len(unified_diff.encode("utf-8"))
    if size > MAX_PATCH_BYTES:
        raise PatchSizeError(f"unified_diff exceeds {MAX_PATCH_BYTES} bytes ({size})")


def _provider_for(handle: SandboxHandle) -> SandboxProvider:
    """Return a provider capable of executing commands in ``handle``.

    The default lookup uses :func:`enten.sandbox.get_provider` with an
    isolated-settings instance. Tests may monkeypatch this function to
    inject a fake provider.
    """
    return get_provider(Settings())


async def generate_patch(
    review_draft: ReviewDraft,
    sandbox: SandboxHandle,
    llm_client: _LLMClient,
) -> Patch:
    """Generate a unified diff patch for ``review_draft``.

    The LLM is given the ``submit_patch`` tool. The returned patch is
    validated for unified-diff format and size before being returned.
    ``sandbox`` is accepted for API consistency with :func:`check_patch`
    and :func:`apply_patch`; this function does not mutate the sandbox.
    """
    _ = sandbox

    prompt = load_prompt("patch", version=1)
    system, user = render(
        prompt,
        {
            "diff": wrap_untrusted(review_draft.content, "diff"),
            "problem": wrap_untrusted("", "pr_description"),
        },
    )

    collected: list[tuple[str, str]] = []

    async def _execute(tool: ToolUse) -> str:
        if tool.name != SUBMIT_PATCH_SCHEMA.name:
            return f"unknown tool: {tool.name}"
        args = tool.arguments
        unified_diff = cast(str, args.get("unified_diff", ""))
        rationale = cast(str, args.get("rationale", ""))
        collected.append((unified_diff, rationale))
        return "saved patch"

    agent_loop = AgentLoopSpec(tools=[SUBMIT_PATCH_SCHEMA], execute=_execute)

    run_id = review_draft.run_id or f"patch-{uuid.uuid4().hex}"
    _ = await llm_client.complete_with_tools(
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
        ],
        tools=[SUBMIT_PATCH_SCHEMA],
        agent_loop=agent_loop,
        run_id=run_id,
    )

    if not collected:
        raise PatchInvalidError("LLM did not submit a patch")

    unified_diff, rationale = collected[-1]

    _validate_format(unified_diff)
    _validate_size(unified_diff)

    sha256 = hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()
    return Patch(
        unified_diff=unified_diff,
        sha256=sha256,
        rationale=rationale,
        applied_in="sandbox",
    )


async def check_patch(sandbox: SandboxHandle, unified_diff: str) -> None:
    """Validate ``unified_diff`` with ``git apply --check`` in ``sandbox``."""
    provider = _provider_for(sandbox)
    await provider.upload(sandbox, {"patch.diff": unified_diff})
    result = await provider.run(
        sandbox,
        ["git", "apply", "--check", "patch.diff"],
        timeout_seconds=120,
    )
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PatchInvalidError(
            f"git apply --check failed (exit_code={result.exit_code}): {detail}"
        )


async def apply_patch(sandbox: SandboxHandle, unified_diff: str) -> None:
    """Apply ``unified_diff`` with ``git apply`` in ``sandbox``.

    This function never accepts ``owner``, ``repo``, or ``branch``
    parameters; application is sandbox-only.
    """
    provider = _provider_for(sandbox)
    await provider.upload(sandbox, {"patch.diff": unified_diff})
    result = await provider.run(
        sandbox,
        ["git", "apply", "patch.diff"],
        timeout_seconds=120,
    )
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PatchApplyError(f"git apply failed (exit_code={result.exit_code}): {detail}")


__all__ = [
    "MAX_PATCH_BYTES",
    "Patch",
    "PatchApplyError",
    "PatchInvalidError",
    "PatchSizeError",
    "apply_patch",
    "check_patch",
    "generate_patch",
]
