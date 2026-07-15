"""Unit tests for the tool JSON parsers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from enten.parsers import (
    ParserError,
    fixture,
    parse_gitleaks_json,
    parse_pytest_json,
    parse_pytest_junit_xml,
    parse_semgrep_json,
    parse_vitest_json,
)
from enten.parsers.runners import _repo_files, run_pytest
from enten.sandbox.models import ExecResult, SandboxHandle


class CanonicalSandbox:
    def __init__(self, report: str, exit_code: int) -> None:
        self.report = report
        self.exit_code = exit_code
        self.destroyed = False

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        return SandboxHandle(id="canonical", provider="fake", image="fake")

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        if cmd[0] == "pytest":
            return ExecResult(
                exit_code=self.exit_code,
                stdout="",
                stderr="runner failed",
                duration_ms=1,
            )
        return ExecResult(
            exit_code=0,
            stdout=self.report,
            stderr="",
            duration_ms=1,
        )

    async def upload(
        self,
        handle: SandboxHandle,
        files: dict[str, str | bytes],
    ) -> None:
        pass

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroyed = True


def test_parse_pytest_json_pass() -> None:
    data = fixture("pytest_pass.json")
    result = parse_pytest_json(data)
    assert result.exit_code == 0
    assert result.summary.passed == 2
    assert result.summary.failed == 0
    assert len(result.tests) == 2
    assert result.tests[0].node_id == "test_demo.py::test_one"
    assert result.tests[0].file == "test_demo.py"
    assert result.tests[0].line == 3


def test_parse_pytest_json_fail() -> None:
    data = fixture("pytest_fail.json")
    result = parse_pytest_json(data)
    assert result.exit_code == 1
    assert result.summary.passed == 1
    assert result.summary.failed == 1
    failed = [t for t in result.tests if t.outcome == "failed"][0]
    assert failed.message == "assert 1 == 2"


def test_parse_pytest_json_invalid_raises() -> None:
    with pytest.raises(ParserError):
        parse_pytest_json("not json")


def test_parse_pytest_junit_preserves_process_exit_and_locations() -> None:
    report = (
        '<testsuites><testsuite tests="2" failures="1" errors="0" skipped="0" time="0.3">'
        '<testcase name="test_ok" file="tests/test_demo.py" line="4" time="0.1" />'
        '<testcase name="test_bad" file="tests/test_demo.py" line="8" time="0.2">'
        '<failure message="assertion failed">details</failure></testcase>'
        "</testsuite></testsuites>"
    )

    result = parse_pytest_junit_xml(report, process_exit_code=2)

    assert result.exit_code == 2
    assert result.summary.total == 2
    assert result.summary.passed == 1
    assert result.summary.failed == 1
    assert result.tests[1].node_id == "tests/test_demo.py::test_bad"
    assert result.tests[1].line == 9
    assert result.tests[1].message == "assertion failed"


def test_parse_pytest_junit_rejects_missing_suite() -> None:
    with pytest.raises(ParserError, match="testsuite"):
        parse_pytest_junit_xml("<root />", process_exit_code=0)


def test_parse_pytest_junit_keeps_zero_collection_exit() -> None:
    report = '<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0" /></testsuites>'

    result = parse_pytest_junit_xml(report, process_exit_code=5)

    assert result.exit_code == 5
    assert result.summary.total == 0


def test_parse_vitest_json_basic() -> None:
    data = fixture("vitest_basic.json")
    result = parse_vitest_json(data)
    assert result.exit_code == 0
    assert result.num_passed_tests == 3
    assert result.num_failed_tests == 0
    assert len(result.test_results) == 3
    assert result.test_results[0].name == "add > adds two numbers"


def test_parse_vitest_json_invalid_raises() -> None:
    with pytest.raises(ParserError):
        parse_vitest_json("[]")


def test_parse_semgrep_json_finding() -> None:
    data = fixture("semgrep_finding.json")
    result = parse_semgrep_json(data)
    assert result.exit_code == 1
    assert len(result.results) == 1
    finding = result.results[0]
    assert finding.check_id == "python.lang.security.audit.eval-detected"
    assert finding.path == "src/app.py"
    assert finding.start_line == 10
    assert finding.end_line == 10
    assert finding.severity == "ERROR"


def test_parse_semgrep_json_invalid_raises() -> None:
    with pytest.raises(ParserError):
        parse_semgrep_json('{"results": 42}')


def test_parse_semgrep_json_never_exposes_arbitrary_error_evidence() -> None:
    result = parse_semgrep_json(
        json.dumps(
            {
                "results": [],
                "errors": [{"message": "scanner leaked token=super-secret-value"}],
            }
        )
    )

    assert "super-secret-value" not in result.model_dump_json()
    assert result.errors == ["scanner_error"]


def test_parse_gitleaks_json_leak() -> None:
    data = fixture("gitleaks_leak.json")
    result = parse_gitleaks_json(data)
    assert result.exit_code == 1
    assert len(result.leaks) == 1
    leak = result.leaks[0]
    assert leak.rule_id == "aws-access-key-id"
    assert leak.file == "config/aws.env"
    assert leak.secret_redacted == "[REDACTED]"
    assert "AKIAIOSFODNN7EXAMPLE" not in result.model_dump_json()


def test_parse_gitleaks_json_invalid_raises() -> None:
    with pytest.raises(ParserError):
        parse_gitleaks_json("{}")


def test_repo_collection_excludes_ignored_secrets_caches_binaries_and_symlinks(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('safe')\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=raw-secret\n", encoding="utf-8")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "state").write_text("cache", encoding="utf-8")
    (repo / "binary.dat").write_bytes(b"\x00\x01\x02")
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (repo / "linked.py").symlink_to(outside)
    subprocess_result = subprocess.run(
        ["git", "init", "-q", str(repo)],
        capture_output=True,
        check=False,
    )
    assert subprocess_result.returncode == 0

    files = _repo_files(str(repo))

    assert "src/app.py" in files
    assert "ignored.txt" not in files
    assert ".env" not in files
    assert ".pytest_cache/state" not in files
    assert "binary.dat" not in files
    assert "linked.py" not in files


async def test_runner_uses_canonical_lifecycle_and_actual_exit_code(
    tmp_path: Path,
) -> None:
    report = (
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_ok" file="test_demo.py" line="0" />'
        "</testsuite></testsuites>"
    )
    sandbox = CanonicalSandbox(report, exit_code=2)

    result = await run_pytest(sandbox, str(tmp_path))

    assert result.exit_code == 2
    assert sandbox.destroyed
