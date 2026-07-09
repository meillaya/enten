"""JSON parsers for pytest, vitest, semgrep and gitleaks outputs."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from enten.parsers.models import (
    GitleaksFinding,
    GitleaksResult,
    PytestResult,
    PytestSummary,
    PytestTest,
    SemgrepFinding,
    SemgrepResult,
    VitestResult,
)


class ParserError(Exception):
    """Raised when tool output cannot be parsed as the expected JSON schema."""


FixturePath = Path(__file__).parent / "fixtures"


def _load_json(data: bytes | str) -> Any:
    """Decode ``bytes``/``str`` to a Python object, raising ``ParserError`` on failure."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    try:
        return json.loads(data)
    except JSONDecodeError as exc:
        raise ParserError(f"Invalid JSON: {exc}") from exc


def _pytest_message(test: dict[str, Any]) -> str | None:
    """Extract a human-readable failure message from pytest stage data."""
    test_outcome = test.get("outcome")
    for stage in ("call", "setup", "teardown"):
        stage_data = test.get(stage)
        if not isinstance(stage_data, dict):
            continue
        stage_outcome = stage_data.get("outcome")
        if stage_outcome in ("failed", "error") or (
            stage_outcome is None and test_outcome in ("failed", "error")
        ):
            longrepr = stage_data.get("longrepr")
            if isinstance(longrepr, str):
                return longrepr
            if isinstance(longrepr, dict):
                crash = longrepr.get("reprcrash") or {}
                if isinstance(crash, dict):
                    msg = crash.get("message")
                    if msg:
                        return msg
                entries = longrepr.get("reprtraceback", {}).get("reprentries", [])
                if entries:
                    first = entries[0]
                    if isinstance(first, dict):
                        data = first.get("data", {})
                        if isinstance(data, dict):
                            msg = data.get("message")
                            if msg:
                                return msg
    return None


def parse_pytest_json(data: bytes | str) -> PytestResult:
    """Parse the output of ``pytest --json-report``.

    The exit code is inferred from the summary: any failed or errored tests
    produce ``exit_code=1``.
    """
    report = _load_json(data)
    if not isinstance(report, dict):
        raise ParserError("pytest JSON report must be an object")

    raw_summary = report.get("summary") or {}
    if not isinstance(raw_summary, dict):
        raise ParserError("pytest JSON summary must be an object")

    summary = PytestSummary.model_validate(raw_summary)

    raw_tests = report.get("tests") or []
    if not isinstance(raw_tests, list):
        raise ParserError("pytest JSON tests must be a list")

    tests: list[PytestTest] = []
    for raw_test in raw_tests:
        if not isinstance(raw_test, dict):
            raise ParserError("pytest JSON test item must be an object")
        node_id = raw_test.get("nodeid") or ""
        file_path = node_id.split("::")[0] if "::" in node_id else raw_test.get("filename")
        tests.append(
            PytestTest.model_validate(
                {
                    "nodeid": node_id,
                    "outcome": raw_test.get("outcome", "unknown"),
                    "duration": raw_test.get("duration", 0.0),
                    "message": _pytest_message(raw_test),
                    "file": file_path,
                    "line": raw_test.get("lineno"),
                }
            )
        )

    exit_code = 1 if summary.failed > 0 or summary.error > 0 else 0
    return PytestResult(exit_code=exit_code, summary=summary, tests=tests)


def parse_vitest_json(data: bytes | str) -> VitestResult:
    """Parse the output of ``vitest run --reporter=json``.

    The exit code is inferred from ``numFailedTests``.
    """
    report = _load_json(data)
    if not isinstance(report, dict):
        raise ParserError("vitest JSON report must be an object")

    try:
        result = VitestResult.model_validate(report)
    except ValidationError as exc:
        raise ParserError(f"vitest JSON validation failed: {exc}") from exc

    result.exit_code = 1 if result.num_failed_tests > 0 else 0
    return result


def parse_semgrep_json(data: bytes | str) -> SemgrepResult:
    """Parse the output of ``semgrep --json``.

    The exit code is inferred from the presence of findings or errors.
    """
    report = _load_json(data)
    if not isinstance(report, dict):
        raise ParserError("semgrep JSON must be an object")

    raw_results = report.get("results") or []
    if not isinstance(raw_results, list):
        raise ParserError("semgrep JSON results must be a list")

    raw_errors = report.get("errors") or []
    if not isinstance(raw_errors, list):
        raise ParserError("semgrep JSON errors must be a list")

    findings: list[SemgrepFinding] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ParserError("semgrep finding must be an object")
        start = raw.get("start") or {}
        end = raw.get("end") or {}
        extra = raw.get("extra") or {}
        findings.append(
            SemgrepFinding.model_validate(
                {
                    "check_id": raw.get("check_id", ""),
                    "path": raw.get("path", ""),
                    "start_line": start.get("line"),
                    "end_line": end.get("line"),
                    "message": extra.get("message", ""),
                    "severity": extra.get("severity", ""),
                }
            )
        )

    exit_code = 1 if findings or raw_errors else 0
    return SemgrepResult(exit_code=exit_code, results=findings, errors=raw_errors)


def parse_gitleaks_json(data: bytes | str) -> GitleaksResult:
    """Parse the output of ``gitleaks detect --report-format json``.

    The ``Secret`` field is redacted to ``[REDACTED]`` in the parsed model.
    The exit code is inferred from the presence of leaks.
    """
    report = _load_json(data)
    if not isinstance(report, list):
        raise ParserError("gitleaks JSON must be a list")

    leaks: list[GitleaksFinding] = []
    for raw in report:
        if not isinstance(raw, dict):
            raise ParserError("gitleaks leak must be an object")
        leaks.append(GitleaksFinding.model_validate(raw))

    exit_code = 1 if leaks else 0
    return GitleaksResult(exit_code=exit_code, leaks=leaks)


def fixture(name: str) -> bytes:
    """Load a fixture file packaged with this module."""
    return (FixturePath / name).read_bytes()
