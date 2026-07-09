"""Flaky test handler.

Runs the same test selector across multiple fresh sandboxes and classifies the
aggregate outcome based on how many runs pass.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class Run(BaseModel):
    """A single sandbox run in a flaky-test series."""

    exit_code: int
    summary: dict[str, Any]
    duration_ms: int
    sandbox_id: str


class FlakyRunSummary(BaseModel):
    """Aggregate result of a flaky-test series."""

    runs: list[Run]
    outcome: Literal["pass", "flaky", "fail", "error"]
    majority_pass_count: int


class SandboxHandle(Protocol):
    """Minimal sandbox handle interface used by the flaky handler."""

    @property
    def id(self) -> str: ...
    async def destroy(self) -> None: ...


class SandboxFactory(Protocol):
    """Minimal sandbox factory interface used by the flaky handler."""

    async def create(self) -> SandboxHandle: ...


Runner = Callable[[SandboxHandle, Any], Awaitable[Any]]


def _result_to_summary(result: Any) -> dict[str, Any]:
    """Convert a runner result into a plain dict for storage."""
    if isinstance(result, BaseModel):
        return result.model_dump()
    if hasattr(result, "_asdict"):  # namedtuple
        return result._asdict()
    if isinstance(result, dict):
        return dict(result)
    return {"value": str(result)}


async def run_with_flaky_check(
    sandbox_factory: SandboxFactory,
    runner: Runner,
    test_selector: Any,
    *,
    runs: int = 3,
) -> FlakyRunSummary:
    """Run ``test_selector`` in ``runs`` fresh sandboxes and classify flakiness.

    Each run uses a separate sandbox to avoid state carry-over. Sandboxes are
    destroyed even when the runner raises an infrastructure error.
    """
    completed_runs: list[Run] = []
    error_occurred = False

    for _ in range(runs):
        handle: SandboxHandle | None = None
        try:
            handle = await sandbox_factory.create()
            start = time.monotonic()
            result = await runner(handle, test_selector)
            duration_ms = int((time.monotonic() - start) * 1000)
            completed_runs.append(
                Run(
                    exit_code=getattr(result, "exit_code", 1),
                    summary=_result_to_summary(result),
                    duration_ms=duration_ms,
                    sandbox_id=handle.id,
                )
            )
        except Exception:
            error_occurred = True
        finally:
            if handle is not None:
                try:
                    await handle.destroy()
                except Exception:
                    error_occurred = True

    pass_count = sum(1 for run in completed_runs if run.exit_code == 0)

    if error_occurred:
        return FlakyRunSummary(
            runs=completed_runs,
            outcome="error",
            majority_pass_count=pass_count,
        )

    if pass_count == runs:
        outcome: Literal["pass", "flaky", "fail"] = "pass"
    elif pass_count == 0:
        outcome = "fail"
    else:
        outcome = "flaky"

    return FlakyRunSummary(
        runs=completed_runs,
        outcome=outcome,
        majority_pass_count=pass_count,
    )
