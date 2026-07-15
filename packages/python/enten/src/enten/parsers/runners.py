"""Sandbox runners that invoke pytest, vitest, semgrep and gitleaks.

The runners construct a fixed command, execute it inside a sandbox, fetch its
report, and parse it into typed verification evidence.
"""

from __future__ import annotations

from enten.parsers import (
    parse_gitleaks_json,
    parse_pytest_junit_xml,
    parse_semgrep_json,
    parse_vitest_json,
)
from enten.parsers.models import (
    GitleaksResult,
    PytestResult,
    PytestSummary,
    SemgrepResult,
    VitestResult,
)
from enten.sandbox.files import collect_repository_files
from enten.sandbox.models import SandboxHandle
from enten.sandbox.protocol import SandboxProvider


def _repo_files(repo_path: str) -> dict[str, str]:
    """Read every file under ``repo_path`` into a flat ``{relative_path: text}`` map."""
    return collect_repository_files(repo_path)


async def _cat_file(
    sandbox: SandboxProvider,
    handle: SandboxHandle,
    path: str,
) -> str:
    """Read ``path`` from the sandbox via ``cat``."""
    result = await sandbox.run(handle, ["cat", path], timeout_seconds=10)
    return result.stdout


async def run_pytest(
    sandbox: SandboxProvider,
    repo_path: str,
    target: str = ".",
    *,
    collect_only: bool = False,
) -> PytestResult:
    """Run pytest in a sandbox and parse the report.

    When ``collect_only`` is ``True``, pytest is invoked with
    ``--collect-only`` and a synthetic :class:`PytestResult` is returned
    with only the exit code populated. This is used by the test generator
    to validate generated tests without executing them.
    """
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        if collect_only:
            result = await sandbox.run(
                handle,
                ["pytest", "--collect-only", "-q", target],
                timeout_seconds=120,
            )
            return PytestResult(
                exit_code=result.exit_code,
                summary=PytestSummary(),
                tests=[],
            )
        command_result = await sandbox.run(
            handle,
            [
                "pytest",
                "-o",
                "junit_family=legacy",
                "--junitxml=/tmp/pytest-report.xml",
                target,
            ],
            timeout_seconds=120,
        )
        xml_text = await _cat_file(sandbox, handle, "/tmp/pytest-report.xml")
        return parse_pytest_junit_xml(
            xml_text,
            process_exit_code=command_result.exit_code,
        )
    finally:
        await sandbox.destroy(handle)


async def run_vitest(sandbox: SandboxProvider, repo_path: str, target: str = ".") -> VitestResult:
    """Run ``vitest run --reporter=json`` in a sandbox and parse the report."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        command_result = await sandbox.run(
            handle,
            ["vitest", "run", "--reporter=json", "--outputFile=/tmp/vitest-report.json", target],
            timeout_seconds=120,
        )
        json_text = await _cat_file(sandbox, handle, "/tmp/vitest-report.json")
        parsed = parse_vitest_json(json_text)
        return parsed.model_copy(update={"exit_code": command_result.exit_code})
    finally:
        await sandbox.destroy(handle)


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
        result = await sandbox.run(handle, cmd, timeout_seconds=120)
        parsed = parse_semgrep_json(result.stdout)
        return parsed.model_copy(update={"exit_code": result.exit_code})
    finally:
        await sandbox.destroy(handle)


async def run_gitleaks(sandbox: SandboxProvider, repo_path: str) -> GitleaksResult:
    """Run ``gitleaks detect`` in a sandbox and parse the JSON report."""
    handle = await sandbox.create(_repo_files(repo_path))
    try:
        command_result = await sandbox.run(
            handle,
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
        json_text = await _cat_file(sandbox, handle, "/tmp/gitleaks-report.json")
        parsed = parse_gitleaks_json(json_text)
        return parsed.model_copy(update={"exit_code": command_result.exit_code})
    finally:
        await sandbox.destroy(handle)
