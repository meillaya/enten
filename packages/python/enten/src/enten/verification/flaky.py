"""Flaky test handler.

Runs the same test selector across multiple fresh sandboxes and classifies the
aggregate outcome based on how many runs pass.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

from enten.sandbox.models import ExecResult

SelectorT = TypeVar("SelectorT")


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    total: int = 0


class Run(BaseModel):
    """A single sandbox run in a flaky-test series."""

    exit_code: int
    summary: RunSummary
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


class SandboxFactory(Protocol):
    """Minimal sandbox factory interface used by the flaky handler."""

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle: ...
    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult: ...
    async def upload(
        self,
        handle: SandboxHandle,
        files: dict[str, str | bytes],
    ) -> None: ...
    async def destroy(self, handle: SandboxHandle) -> None: ...


class RunnerResult(Protocol):
    exit_code: int


Runner = Callable[[SandboxHandle, SelectorT], Awaitable[RunnerResult]]


def _result_to_summary(result: RunnerResult) -> RunSummary:
    return RunSummary(exit_code=result.exit_code)


async def run_with_flaky_check(
    sandbox_factory: SandboxFactory,
    runner: Runner[SelectorT],
    test_selector: SelectorT,
    *,
    runs: int = 3,
) -> FlakyRunSummary:
    """Run ``test_selector`` in ``runs`` fresh sandboxes and classify flakiness.

    Each run uses a separate sandbox to avoid state carry-over. Sandboxes are
    destroyed even when the runner raises an infrastructure error.
    """
    if runs < 1:
        raise ValueError("runs must be positive")
    completed_runs: list[Run] = []
    error_occurred = False

    for _ in range(runs):
        handle: SandboxHandle | None = None
        try:
            handle = await sandbox_factory.create({})
            start = time.monotonic()
            result = await runner(handle, test_selector)
            duration_ms = int((time.monotonic() - start) * 1000)
            completed_runs.append(
                Run(
                    exit_code=result.exit_code,
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
                    await sandbox_factory.destroy(handle)
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
    elif pass_count * 3 >= runs * 2:
        outcome = "flaky"
    else:
        outcome = "fail"

    return FlakyRunSummary(
        runs=completed_runs,
        outcome=outcome,
        majority_pass_count=pass_count,
    )
