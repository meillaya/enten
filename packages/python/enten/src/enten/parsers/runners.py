"""Sandbox runners that invoke pytest, vitest, semgrep and gitleaks.

These runners are intentionally thin: they construct the command, execute it
inside a sandbox, fetch the JSON report (when written to a file), and parse it.
They are not integration-tested here; T22/T34 will exercise them against a
real ``SandboxProvider`` implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from enten.parsers import (
    parse_gitleaks_json,
    parse_pytest_json,
    parse_semgrep_json,
    parse_vitest_json,
)
from enten.parsers.models import (
    GitleaksResult,
    PytestResult,
    SemgrepResult,
    VitestResult,
)


class ExecResult(Protocol):
    """Result of a sandbox command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class SandboxHandle(Protocol):
    """Handle to a running sandbox instance."""

    async def run(self, cmd: list[str], timeout_seconds: int = 120) -> ExecResult: ...
    async def upload(self, files: dict[str, str | bytes]) -> None: ...
    async def destroy(self) -> None: ...


class SandboxProvider(Protocol):
    """Factory for creating hermetic sandbox instances."""

    @staticmethod
    async def create(repo_files: dict[str, str]) -> SandboxHandle: ...


def _repo_files(repo_path: str) -> dict[str, str]:
    """Read every file under ``repo_path`` into a flat ``{relative_path: text}`` map."""
    files: dict[str, str] = {}
    base = Path(repo_path)
    if not base.exists():
        return files
    for path in base.rglob("*"):
        if path.is_file():
            rel = path.relative_to(base).as_posix()
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[rel] = path.read_bytes().decode("utf-8", errors="replace")
    return files


async def _cat_file(handle: SandboxHandle, path: str) -> str:
    """Read ``path`` from the sandbox via ``cat``."""
    result = await handle.run(["cat", path], timeout_seconds=10)
    return result.stdout


async def run_pytest(
    sandbox: SandboxProvider, repo_path: str, target: str = "."
) -> PytestResult:
    """Run ``pytest --json-report`` in a sandbox and parse the report."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        _ = await handle.run(
            ["pytest", "--json-report", "--json-report-file=/tmp/pytest-report.json", target],
            timeout_seconds=120,
        )
        json_text = await _cat_file(handle, "/tmp/pytest-report.json")
        return parse_pytest_json(json_text)
    finally:
        await handle.destroy()


async def run_vitest(
    sandbox: SandboxProvider, repo_path: str, target: str = "."
) -> VitestResult:
    """Run ``vitest run --reporter=json`` in a sandbox and parse the report."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        _ = await handle.run(
            ["vitest", "run", "--reporter=json", "--outputFile=/tmp/vitest-report.json", target],
            timeout_seconds=120,
        )
        json_text = await _cat_file(handle, "/tmp/vitest-report.json")
        return parse_vitest_json(json_text)
    finally:
        await handle.destroy()


async def run_semgrep(
    sandbox: SandboxProvider,
    repo_path: str,
    custom_rules: list[str] | None = None,
) -> SemgrepResult:
    """Run ``semgrep --json`` in a sandbox and parse the results."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        cmd = ["semgrep", "--json", "--config", "auto", "--error"]
        if custom_rules:
            for rule in custom_rules:
                cmd.extend(["--config", rule])
        cmd.append("/workspace")
        result = await handle.run(cmd, timeout_seconds=120)
        return parse_semgrep_json(result.stdout)
    finally:
        await handle.destroy()


async def run_gitleaks(sandbox: SandboxProvider, repo_path: str) -> GitleaksResult:
    """Run ``gitleaks detect`` in a sandbox and parse the JSON report."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        _ = await handle.run(
            [
                "gitleaks",
                "detect",
                "--no-git",
                "--report-format",
                "json",
                "--report-path",
                "/tmp/gitleaks-report.json",
                "--exit-code",
                "1",
            ],
            timeout_seconds=120,
        )
        json_text = await _cat_file(handle, "/tmp/gitleaks-report.json")
        return parse_gitleaks_json(json_text)
    finally:
        await handle.destroy()
