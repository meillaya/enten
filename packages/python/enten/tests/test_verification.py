"""Tests for the deterministic verification arbiter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from enten.sandbox.models import ExecResult
from enten.verification import VerificationResult, verify
from enten.verification.flaky import SandboxHandle


class FakeHandle:
    """Sandbox handle that replays a canned pytest JSON report."""

    def __init__(self, sandbox_id: str, report_json: str, exit_code: int) -> None:
        self.id = sandbox_id
        self.report_json = report_json
        self.exit_code = exit_code

    async def run(self, cmd: list[str], timeout_seconds: int = 120) -> ExecResult:
        if cmd[0] == "pytest":
            return ExecResult(
                exit_code=self.exit_code,
                stdout="",
                stderr="",
                duration_ms=100,
            )
        if cmd[0] == "cat":
            return ExecResult(
                exit_code=0,
                stdout=self.report_json,
                stderr="",
                duration_ms=10,
            )
        return ExecResult(
            exit_code=1,
            stdout="",
            stderr="unknown command",
            duration_ms=0,
        )

    async def upload(self, files: dict[str, str | bytes]) -> None:
        pass

    async def destroy(self) -> None:
        pass


class FakeSandboxFactory:
    """Creates the pre-canned handles in a deterministic order."""

    def __init__(self, handles: list[FakeHandle]) -> None:
        self.handles = handles
        self._index = 0

    async def create(self, repo_files: dict[str, str]) -> FakeHandle:
        _ = repo_files
        handle = self.handles[self._index]
        self._index += 1
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        _ = handle

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        matching = next(item for item in self.handles if item.id == handle.id)
        return await matching.run(cmd, timeout_seconds=timeout_seconds)

    async def upload(
        self,
        handle: SandboxHandle,
        files: dict[str, str | bytes],
    ) -> None:
        matching = next(item for item in self.handles if item.id == handle.id)
        await matching.upload(files)


def _passing_report() -> str:
    return (
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1">'
        '<testcase name="test_one" file="test_demo.py" line="0" time="0.1" />'
        "</testsuite></testsuites>"
    )


def _failing_report() -> str:
    return (
        '<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0" time="0.1">'
        '<testcase name="test_one" file="test_demo.py" line="0" time="0.1">'
        '<failure message="assert 1 == 2">details</failure></testcase>'
        "</testsuite></testsuites>"
    )


@pytest.fixture
def deterministic_time(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make durations inside the flaky handler deterministic and resettable."""
    import enten.verification.flaky as flaky_mod

    counter = 0.0

    def monotonic() -> float:
        nonlocal counter
        counter += 1.0
        return counter

    def reset() -> None:
        nonlocal counter
        counter = 0.0

    monkeypatch.setattr(flaky_mod.time, "monotonic", monotonic)
    return reset


async def test_verify_is_deterministic_for_same_fixture(
    tmp_path: Path,
    deterministic_time: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs against the same fixture must produce byte-identical evidence."""
    report = _passing_report()
    factory1 = FakeSandboxFactory([FakeHandle(f"sbx-{i}", report, 0) for i in range(3)])
    result1 = await verify(
        "run-1",
        sandbox_factory=factory1,
        repo_path=str(tmp_path),
        language="python",
        target_selector=".",
    )

    deterministic_time()
    factory2 = FakeSandboxFactory([FakeHandle(f"sbx-{i}", report, 0) for i in range(3)])
    result2 = await verify(
        "run-1",
        sandbox_factory=factory2,
        repo_path=str(tmp_path),
        language="python",
        target_selector=".",
    )

    assert result1.model_dump_json() == result2.model_dump_json()
    assert isinstance(result1, VerificationResult)
    assert result1.outcome == "pass"
    assert result1.exit_codes == [0, 0, 0]
    assert not result1.flaky


async def test_verify_reports_fail_on_pytest_failure(
    tmp_path: Path,
    deterministic_time: Any,
) -> None:
    """A consistently failing test target yields a ``fail`` verdict."""
    report = _failing_report()
    factory = FakeSandboxFactory([FakeHandle(f"sbx-{i}", report, 1) for i in range(3)])

    result = await verify(
        "run-fail",
        sandbox_factory=factory,
        repo_path=str(tmp_path),
        language="python",
        target_selector="test_demo.py::test_one",
    )

    assert result.outcome == "fail"
    assert result.exit_codes == [1, 1, 1]
    assert not result.flaky


async def test_verify_treats_command_exit_as_authoritative_over_passing_report(
    tmp_path: Path,
    deterministic_time: Any,
) -> None:
    report = _passing_report()
    factory = FakeSandboxFactory([FakeHandle(f"sbx-{i}", report, 2) for i in range(3)])

    result = await verify(
        "run-command-failed",
        sandbox_factory=factory,
        repo_path=str(tmp_path),
        language="python",
        target_selector=".",
    )

    assert result.outcome == "fail"
    assert result.exit_codes == [2, 2, 2]


async def test_verify_reports_error_on_bogus_target(
    tmp_path: Path,
    deterministic_time: Any,
) -> None:
    """A target that cannot be parsed produces an ``error`` verdict."""
    factory = FakeSandboxFactory([FakeHandle(f"sbx-{i}", "not valid json", 5) for i in range(3)])

    result = await verify(
        "run-error",
        sandbox_factory=factory,
        repo_path=str(tmp_path),
        language="python",
        target_selector="nonexistent.py",
    )

    assert result.outcome == "error"


def test_verify_module_contains_no_llm_calls() -> None:
    """The arbiter must never invoke an LLM completion or stream method."""
    verification_root = Path(__file__).resolve().parents[1] / "src" / "enten" / "verification"
    proc = subprocess.run(
        ["grep", "-rnE", r"(\.complete\(|\.stream\()", str(verification_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1 or proc.stdout == "", f"Found LLM calls:\n{proc.stdout}"
