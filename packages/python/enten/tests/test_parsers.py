"""Unit tests for the tool JSON parsers."""

from __future__ import annotations

import pytest

from enten.parsers import (
    ParserError,
    fixture,
    parse_gitleaks_json,
    parse_pytest_json,
    parse_semgrep_json,
    parse_vitest_json,
)


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
        parse_semgrep_json("{\"results\": 42}")


def test_parse_gitleaks_json_leak() -> None:
    data = fixture("gitleaks_leak.json")
    result = parse_gitleaks_json(data)
    assert result.exit_code == 1
    assert len(result.leaks) == 1
    leak = result.leaks[0]
    assert leak.rule_id == "aws-access-key-id"
    assert leak.file == "config/aws.env"
    assert leak.secret_redacted == "[REDACTED]"


def test_parse_gitleaks_json_invalid_raises() -> None:
    with pytest.raises(ParserError):
        parse_gitleaks_json("{}")
