"""Tests for the security analysis runner.

These tests use a fake :class:`SandboxProvider` so they exercise the runner
without requiring Docker or real security tools.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from enten.sandbox.models import ExecResult, SandboxHandle
from enten.static_analysis.security import (
    SecurityFinding,
    SecurityReport,
    run_security_checks,
)

_AWS_KEY_RE = re.compile(r"AKIA[A-Z0-9]{16}")


class FakeSandboxProvider:
    """In-memory sandbox provider that simulates gitleaks, bandit and semgrep."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    async def create(self, repo_files: dict[str, str]) -> SandboxHandle:
        self._files = repo_files
        return SandboxHandle(id="fake", provider="fake", image="fake")

    async def run(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        timeout_seconds: int = 120,
    ) -> ExecResult:
        if cmd[:2] == ["sh", "-c"] and cmd[2].startswith("command -v"):
            tool = cmd[2].split()[-1]
            return ExecResult(exit_code=0, stdout=f"/usr/bin/{tool}", stderr="", duration_ms=1)

        if cmd[0] == "gitleaks":
            has_leak = self._has_aws_key()
            return ExecResult(exit_code=1 if has_leak else 0, stdout="", stderr="", duration_ms=1)

        if cmd == ["cat", "/tmp/gl.json"]:
            return ExecResult(exit_code=0, stdout=self._gitleaks_json(), stderr="", duration_ms=1)

        if cmd[0] == "bandit":
            has_issue = self._has_password()
            return ExecResult(exit_code=1 if has_issue else 0, stdout="", stderr="", duration_ms=1)

        if cmd == ["cat", "/tmp/bandit.json"]:
            return ExecResult(
                exit_code=0, stdout=self._bandit_json(), stderr="", duration_ms=1
            )

        if cmd[0] == "semgrep":
            return ExecResult(
                exit_code=0,
                stdout=json.dumps({"results": [], "errors": []}),
                stderr="",
                duration_ms=1,
            )

        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    async def upload(
        self, handle: SandboxHandle, files: dict[str, str | bytes]
    ) -> None:
        for name, content in files.items():
            self._files[name] = content.decode("utf-8") if isinstance(content, bytes) else content

    async def destroy(self, handle: SandboxHandle) -> None:
        pass

    def _has_aws_key(self) -> bool:
        return any(_AWS_KEY_RE.search(content) for content in self._files.values())

    def _aws_key_location(self) -> tuple[str, int, str]:
        for rel_path, content in self._files.items():
            for line_no, line in enumerate(content.splitlines(), start=1):
                match = _AWS_KEY_RE.search(line)
                if match:
                    return rel_path, line_no, match.group(0)
        return "", 0, ""

    def _gitleaks_json(self) -> str:
        if not self._has_aws_key():
            return "[]"
        rel_path, line_no, secret = self._aws_key_location()
        return json.dumps(
            [
                {
                    "File": rel_path,
                    "RuleID": "aws-access-key-id",
                    "StartLine": line_no,
                    "EndLine": line_no,
                    "Match": secret,
                    "Secret": secret,
                }
            ]
        )

    def _has_password(self) -> bool:
        return any('password = "hunter2"' in content for content in self._files.values())

    def _password_location(self) -> tuple[str, int]:
        for rel_path, content in self._files.items():
            for line_no, line in enumerate(content.splitlines(), start=1):
                if 'password = "hunter2"' in line:
                    return rel_path, line_no
        return "", 0

    def _bandit_json(self) -> str:
        if not self._has_password():
            return json.dumps({"results": [], "errors": []})
        rel_path, line_no = self._password_location()
        return json.dumps(
            {
                "results": [
                    {
                        "test_id": "B105",
                        "test_name": "hardcoded_password_string",
                        "issue_severity": "LOW",
                        "issue_confidence": "MEDIUM",
                        "filename": rel_path,
                        "line_number": line_no,
                        "line_range": [line_no],
                        "code": 'password = "hunter2"\n',
                        "issue_text": "Possible hardcoded password",
                    }
                ],
                "errors": [],
            }
        )


@pytest.fixture
def sandbox() -> FakeSandboxProvider:
    return FakeSandboxProvider()


async def test_gitleaks_detects_aws_key_and_redacts_secret(
    sandbox: FakeSandboxProvider, tmp_path: Any
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config").mkdir()
    (repo / "config" / "aws.env").write_text("AWS_ACCESS_KEY_ID=AKIA0000000000000000\n")

    report = await run_security_checks(sandbox, str(repo), "python")

    assert isinstance(report, SecurityReport)
    assert report.counts.get("total") == 1
    assert report.counts.get("gitleaks") == 1

    finding = report.findings[0]
    assert isinstance(finding, SecurityFinding)
    assert finding.tool == "gitleaks"
    assert finding.check_id == "aws-access-key-id"
    assert finding.file == "config/aws.env"
    assert finding.line == 1
    assert "AKIA0000000000000000" not in finding.message
    assert "***" in finding.message


async def test_bandit_detects_hardcoded_password(
    sandbox: FakeSandboxProvider, tmp_path: Any
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text('password = "hunter2"\n')

    report = await run_security_checks(sandbox, str(repo), "python")

    assert report.counts.get("total") == 1
    assert report.counts.get("bandit") == 1

    finding = report.findings[0]
    assert finding.tool == "bandit"
    assert finding.check_id == "B105"
    assert finding.file == "src/app.py"
    assert finding.line == 1
    assert finding.severity == "LOW"
    assert "hardcoded password" in finding.message.lower()


async def test_clean_repo_has_no_findings(
    sandbox: FakeSandboxProvider, tmp_path: Any
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "utils.py").write_text("x = 1\n")

    report = await run_security_checks(sandbox, str(repo), "python")

    assert report.findings == []
    assert report.counts.get("total", 0) == 0
    assert report.counts.get("gitleaks", 0) == 0
    assert report.counts.get("bandit", 0) == 0
