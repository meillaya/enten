"""Tests for the flaky test handler."""

from __future__ import annotations

from pydantic import BaseModel

from enten.sandbox.models import ExecResult
from enten.verification.flaky import SandboxHandle, run_with_flaky_check


class FakeResult(BaseModel):
    exit_code: int


class FakeSandboxHandle:
    id: str
    destroyed: bool

    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id
        self.destroyed = False

    async def destroy(self) -> None:
        self.destroyed = True


class FakeSandboxFactory:
    call_count: int
    handles: list[FakeSandboxHandle]

    def __init__(self) -> None:
        self.call_count = 0
        self.handles = []

    async def create(self, repo_files: dict[str, str]) -> FakeSandboxHandle:
        _ = repo_files
        self.call_count += 1
        handle = FakeSandboxHandle(f"sandbox-{self.call_count}")
        self.handles.append(handle)
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        matching = next(item for item in self.handles if item.id == handle.id)
        await matching.destroy()

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=0)

    async def upload(
        self,
        handle: SandboxHandle,
        files: dict[str, str | bytes],
    ) -> None:
        pass


class FakeRunner:
    exit_codes: list[int]
    call_count: int

    def __init__(self, exit_codes: list[int]) -> None:
        self.exit_codes = exit_codes
        self.call_count = 0

    async def __call__(self, handle: SandboxHandle, test_selector: str) -> FakeResult:
        code = self.exit_codes[self.call_count]
        self.call_count += 1
        return FakeResult(exit_code=code)


class FakeRunnerRaiseOnSecond:
    async def __call__(self, handle: SandboxHandle, test_selector: str) -> FakeResult:
        if handle.id == "sandbox-2":
            raise RuntimeError("infrastructure failure")
        return FakeResult(exit_code=0)


class FakeRunnerWithSecretEvidence:
    async def __call__(self, handle: SandboxHandle, test_selector: str) -> SecretResult:
        return SecretResult(exit_code=0, evidence="token=super-secret-evidence")


class SecretResult(BaseModel):
    exit_code: int
    evidence: str


async def test_flaky_handler_reports_flaky_on_two_of_three() -> None:
    factory = FakeSandboxFactory()
    runner = FakeRunner([0, 1, 0])
    summary = await run_with_flaky_check(factory, runner, "test_foo")

    assert summary.outcome == "flaky"
    assert summary.majority_pass_count == 2
    assert len(summary.runs) == 3
    assert [r.exit_code for r in summary.runs] == [0, 1, 0]
    assert factory.call_count == 3
    assert all(h.destroyed for h in factory.handles)


async def test_flaky_handler_reports_fail_on_one_of_three() -> None:
    factory = FakeSandboxFactory()
    runner = FakeRunner([1, 0, 1])

    summary = await run_with_flaky_check(factory, runner, "test_foo")

    assert summary.outcome == "fail"
    assert summary.majority_pass_count == 1


async def test_flaky_handler_reports_fail_on_all_fail() -> None:
    factory = FakeSandboxFactory()
    runner = FakeRunner([1, 1, 1])
    summary = await run_with_flaky_check(factory, runner, "test_foo")

    assert summary.outcome == "fail"
    assert summary.majority_pass_count == 0
    assert len(summary.runs) == 3


async def test_flaky_handler_reports_pass_on_all_pass() -> None:
    factory = FakeSandboxFactory()
    runner = FakeRunner([0, 0, 0])
    summary = await run_with_flaky_check(factory, runner, "test_foo")

    assert summary.outcome == "pass"
    assert summary.majority_pass_count == 3
    assert factory.call_count == 3
    assert all(h.destroyed for h in factory.handles)


async def test_flaky_handler_reports_error_on_runner_exception() -> None:
    factory = FakeSandboxFactory()
    runner = FakeRunnerRaiseOnSecond()
    summary = await run_with_flaky_check(factory, runner, "test_foo")

    assert summary.outcome == "error"
    assert all(h.destroyed for h in factory.handles)


async def test_flaky_handler_does_not_store_arbitrary_runner_evidence() -> None:
    factory = FakeSandboxFactory()

    summary = await run_with_flaky_check(
        factory,
        FakeRunnerWithSecretEvidence(),
        "test_foo",
    )

    assert "super-secret-evidence" not in summary.model_dump_json()
