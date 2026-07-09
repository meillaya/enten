"""Pydantic models for test and analysis tool JSON outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PytestSummary(BaseModel):
    """Summary block from a pytest-json-report report."""

    model_config = ConfigDict(extra="ignore")
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error: int = 0
    total: int = 0
    duration: float = 0.0


class PytestTest(BaseModel):
    """Single test case from a pytest-json-report report."""

    model_config = ConfigDict(extra="ignore")
    node_id: str = Field(alias="nodeid")
    outcome: str
    duration: float = 0.0
    message: str | None = None
    file: str | None = None
    line: int | None = None


class PytestResult(BaseModel):
    """Parsed result of ``pytest --json-report``."""

    model_config = ConfigDict(extra="ignore")
    exit_code: int
    summary: PytestSummary
    tests: list[PytestTest] = []


class VitestTestResult(BaseModel):
    """Single test result from a Vitest JSON report."""

    model_config = ConfigDict(extra="ignore")
    name: str
    status: str
    duration: float = 0.0
    failure_messages: list[str] = Field(alias="failureMessages", default=[])
    file: str | None = None
    line: int | None = None


class VitestResult(BaseModel):
    """Parsed result of ``vitest run --reporter=json``."""

    model_config = ConfigDict(extra="ignore")
    exit_code: int = 0
    num_passed_tests: int = Field(alias="numPassedTests", default=0)
    num_failed_tests: int = Field(alias="numFailedTests", default=0)
    num_pending_tests: int = Field(alias="numPendingTests", default=0)
    test_results: list[VitestTestResult] = Field(alias="testResults", default=[])


class SemgrepFinding(BaseModel):
    """A single Semgrep finding."""

    model_config = ConfigDict(extra="ignore")
    check_id: str
    path: str
    start_line: int
    end_line: int
    message: str
    severity: str


class SemgrepResult(BaseModel):
    """Parsed result of ``semgrep --json``."""

    model_config = ConfigDict(extra="ignore")
    exit_code: int
    results: list[SemgrepFinding] = []
    errors: list[Any] = []


class GitleaksFinding(BaseModel):
    """A single gitleaks leak with the secret redacted."""

    model_config = ConfigDict(extra="ignore")
    file: str = Field(alias="File")
    rule_id: str = Field(alias="RuleID")
    start_line: int = Field(alias="StartLine")
    end_line: int = Field(alias="EndLine")
    match: str = Field(alias="Match")
    secret_redacted: str = "[REDACTED]"


class GitleaksResult(BaseModel):
    """Parsed result of ``gitleaks detect`` JSON output."""

    model_config = ConfigDict(extra="ignore")
    exit_code: int
    leaks: list[GitleaksFinding] = []
