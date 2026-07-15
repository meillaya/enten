from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import anyio


def _junit_result(report_path: Path, exit_code: int) -> dict[str, Any]:
    root = ElementTree.parse(report_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    summary = {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    tests: list[dict[str, str]] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            outcome = next(
                (
                    candidate
                    for candidate in ("failure", "error", "skipped")
                    if case.find(candidate) is not None
                ),
                "passed",
            )
            tests.append(
                {
                    "classname": case.attrib.get("classname", ""),
                    "name": case.attrib.get("name", ""),
                    "file": case.attrib.get("file", ""),
                    "line": case.attrib.get("line", ""),
                    "outcome": outcome,
                }
            )
    total = summary.pop("tests")
    passed = exit_code == 0 and total > 0 and not summary["failures"] and not summary["errors"]
    return {
        "outcome": "pass" if passed else "fail",
        "exit_code": exit_code,
        "summary": {"total": total, **summary},
        "tests": tests,
    }


async def _run_once(fixture_dir: Path, report_path: Path) -> dict[str, Any]:
    process = await anyio.run_process(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-o",
            "junit_family=legacy",
            f"--junitxml={report_path}",
            ".",
        ],
        cwd=fixture_dir,
        check=False,
    )
    return _junit_result(report_path, process.returncode)


async def run_determinism_check() -> dict[str, Any]:
    fixture_dir = Path(__file__).resolve().parents[6] / "tests" / "fixtures" / "determinism"
    pr_path = fixture_dir / "pr.json"
    if not pr_path.exists():
        return {"passed": False, "error": f"determinism fixture not found: {pr_path}"}

    with tempfile.TemporaryDirectory(prefix="enten-determinism-") as temporary_dir:
        report_path = Path(temporary_dir) / "pytest.xml"
        first_result = await _run_once(fixture_dir, report_path)
        second_result = await _run_once(fixture_dir, report_path)

    return {
        "passed": first_result == second_result and first_result["outcome"] == "pass",
        "first": first_result,
        "second": second_result,
    }
