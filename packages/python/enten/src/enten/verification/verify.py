"""Deterministic verification arbiter (THE ARBITER).

Runs a test target in fresh sandboxes, classifies the aggregate outcome purely
from sandbox exit codes, and returns structured evidence. No LLM is consulted
inside this module — the verdict is closed-form and reproducible.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel

from enten.parsers import runners as _runners
from enten.sandbox.models import ExecResult
from enten.verification.flaky import SandboxFactory, SandboxHandle, run_with_flaky_check


class RunEvidence(BaseModel):
    """Structured evidence for a single verification run."""

    exit_code: int
    summary: dict[str, Any]
    duration_ms: int
    sandbox_id: str
    command: list[str]
    log_paths: list[str]


class VerificationResult(BaseModel):
    """Aggregate deterministic verdict for a verification request."""

    outcome: Literal["pass", "flaky", "fail", "error"]
    runs: list[RunEvidence]
    exit_codes: list[int]
    flaky: bool
    sandbox_ids: list[str]
    log_paths: list[str]


def _map_outcome(
    flaky_outcome: Literal["pass", "flaky", "fail", "error"],
) -> Literal["pass", "flaky", "fail", "error"]:
    """Translate the flaky handler's aggregate outcome into a verdict."""
    if flaky_outcome == "pass":
        return "pass"
    if flaky_outcome == "flaky":
        return "flaky"
    if flaky_outcome == "fail":
        return "fail"
    return "error"


def _log_paths(language: str) -> list[str]:
    """Report artifact paths produced by the runner for ``language``."""
    if language in {"python", "py"}:
        return ["/tmp/pytest-report.json"]
    if language in {"typescript", "ts", "javascript", "js"}:
        return ["/tmp/vitest-report.json"]
    return []


def _runner_command(language: str, target_selector: str) -> list[str]:
    """Command executed inside the sandbox for ``language`` and ``target_selector``."""
    if language in {"python", "py"}:
        return [
            "pytest",
            "--json-report",
            "--json-report-file=/tmp/pytest-report.json",
            target_selector,
        ]
    if language in {"typescript", "ts", "javascript", "js"}:
        return [
            "vitest",
            "run",
            "--reporter=json",
            "--outputFile=/tmp/vitest-report.json",
            target_selector,
        ]
    return [target_selector]


class RunnableSandboxHandle(SandboxHandle, Protocol):
    """Sandbox handle that can execute commands and accept uploads."""

    async def run(self, cmd: list[str], timeout_seconds: int = 120) -> ExecResult: ...
    async def upload(self, files: dict[str, str | bytes]) -> None: ...
    async def destroy(self) -> None: ...


class _SandboxProviderAdapter:
    """Adapt a SandboxHandle into the SandboxProvider interface expected by runners."""

    _handle: RunnableSandboxHandle

    def __init__(self, handle: RunnableSandboxHandle) -> None:
        self._handle = handle

    async def create(self, repo_files: dict[str, str]) -> RunnableSandboxHandle:
        """Return the handle that was injected by the flaky handler."""
        _ = repo_files
        return self._handle

    async def run(
        self, handle: RunnableSandboxHandle, cmd: list[str], timeout_seconds: int = 120
    ) -> ExecResult:
        """Delegate command execution to the injected handle."""
        _ = handle
        return await self._handle.run(cmd, timeout_seconds=timeout_seconds)

    async def upload(
        self, handle: RunnableSandboxHandle, files: dict[str, str | bytes]
    ) -> None:
        """Delegate file upload to the injected handle."""
        _ = handle
        await self._handle.upload(files)

    async def destroy(self, handle: RunnableSandboxHandle) -> None:
        """No-op: the flaky handler owns sandbox lifecycle."""
        _ = handle


async def verify(
    run_id: str,
    *,
    sandbox_factory: SandboxFactory,
    repo_path: str,
    language: str,
    target_selector: str,
) -> VerificationResult:
    """Verify ``target_selector`` deterministically using sandbox exit codes.

    Args:
        run_id: Correlation identifier for the verification request.
        sandbox_factory: Factory that creates fresh sandbox handles.
        repo_path: Local path to the repository under test.
        language: ``python``/``py`` or ``typescript``/``ts``/``javascript``/``js``.
        target_selector: Test selector passed to the runner (e.g. a file or test id).

    Returns:
        A :class:`VerificationResult` with the closed-form verdict and evidence.
    """
    _ = run_id
    log_paths = _log_paths(language)
    command = _runner_command(language, target_selector)

    async def runner(handle: SandboxHandle, selector: str) -> object:
        adapter = _SandboxProviderAdapter(cast(RunnableSandboxHandle, handle))
        if language in {"python", "py"}:
            return await _runners.run_pytest(adapter, repo_path, selector)
        if language in {"typescript", "ts", "javascript", "js"}:
            return await _runners.run_vitest(adapter, repo_path, selector)
        raise ValueError(f"Unsupported language: {language}")

    summary = await run_with_flaky_check(
        sandbox_factory,
        runner,
        target_selector,
        runs=3,
    )

    outcome = _map_outcome(summary.outcome)

    runs: list[RunEvidence] = []
    for run in summary.runs:
        runs.append(
            RunEvidence(
                exit_code=run.exit_code,
                summary=run.summary,
                duration_ms=run.duration_ms,
                sandbox_id=run.sandbox_id,
                command=command,
                log_paths=log_paths,
            )
        )

    exit_codes = [run.exit_code for run in summary.runs]
    sandbox_ids = [run.sandbox_id for run in summary.runs]

    all_log_paths: list[str] = []
    seen: set[str] = set()
    for run in runs:
        for path in run.log_paths:
            if path not in seen:
                seen.add(path)
                all_log_paths.append(path)

    return VerificationResult(
        outcome=outcome,
        runs=runs,
        exit_codes=exit_codes,
        flaky=outcome == "flaky",
        sandbox_ids=sandbox_ids,
        log_paths=all_log_paths,
    )
