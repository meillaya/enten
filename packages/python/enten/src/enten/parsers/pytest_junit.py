from __future__ import annotations

from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from enten.parsers.errors import ParserError
from enten.parsers.models import PytestResult, PytestSummary, PytestTest


def _integer(element: Element, name: str) -> int:
    try:
        value = int(element.attrib.get(name, "0"))
    except ValueError as exc:
        raise ParserError(f"pytest JUnit {name} must be an integer") from exc
    if value < 0:
        raise ParserError(f"pytest JUnit {name} must not be negative")
    return value


def _duration(element: Element) -> float:
    try:
        value = float(element.attrib.get("time", "0"))
    except ValueError as exc:
        raise ParserError("pytest JUnit time must be numeric") from exc
    if value < 0:
        raise ParserError("pytest JUnit time must not be negative")
    return value


def _tag(element: Element) -> str:
    return element.tag.rsplit("}", maxsplit=1)[-1]


def _suites(root: Element) -> list[Element]:
    if _tag(root) == "testsuite":
        return [root]
    direct = [child for child in root if _tag(child) == "testsuite"]
    if direct:
        return direct
    raise ParserError("pytest JUnit report must contain a testsuite")


def _case(testcase: Element) -> PytestTest:
    outcome = "passed"
    message: str | None = None
    for child in testcase:
        child_tag = _tag(child)
        if child_tag not in {"failure", "error", "skipped"}:
            continue
        outcome = "failed" if child_tag == "failure" else child_tag
        message = child.attrib.get("message") or (child.text or "").strip() or None
        break

    file_path = testcase.attrib.get("file")
    name = testcase.attrib.get("name", "")
    node_id = f"{file_path}::{name}" if file_path else name
    raw_line = testcase.attrib.get("line")
    try:
        line = int(raw_line) + 1 if raw_line is not None else None
    except ValueError as exc:
        raise ParserError("pytest JUnit testcase line must be an integer") from exc
    if line is not None and line < 1:
        raise ParserError("pytest JUnit testcase line must not be negative")

    return PytestTest.model_validate(
        {
            "nodeid": node_id,
            "outcome": outcome,
            "duration": _duration(testcase),
            "message": message,
            "file": file_path,
            "line": line,
        }
    )


def parse_pytest_junit_xml(
    data: bytes | str,
    *,
    process_exit_code: int,
) -> PytestResult:
    """Parse built-in pytest JUnit while preserving the process exit."""
    if process_exit_code < 0:
        raise ParserError("pytest process exit code must not be negative")
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, UnicodeDecodeError) as exc:
        raise ParserError(f"invalid pytest JUnit XML: {exc}") from exc

    total = failed = errors = skipped = 0
    duration = 0.0
    tests: list[PytestTest] = []
    for suite in _suites(root):
        suite_total = _integer(suite, "tests")
        suite_failed = _integer(suite, "failures")
        suite_errors = _integer(suite, "errors")
        suite_skipped = _integer(suite, "skipped")
        if suite_failed + suite_errors + suite_skipped > suite_total:
            raise ParserError("pytest JUnit summary counts are inconsistent")
        total += suite_total
        failed += suite_failed
        errors += suite_errors
        skipped += suite_skipped
        duration += _duration(suite)
        tests.extend(_case(child) for child in suite if _tag(child) == "testcase")

    summary = PytestSummary(
        passed=total - failed - errors - skipped,
        failed=failed,
        skipped=skipped,
        error=errors,
        total=total,
        duration=duration,
    )
    return PytestResult(exit_code=process_exit_code, summary=summary, tests=tests)


__all__ = ["parse_pytest_junit_xml"]
