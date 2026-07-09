"""Tests for stacktrace-to-symbol failure attribution."""

from __future__ import annotations

import json
from typing import Any

import pytest

from enten.failure_attribution import Attribution, attribute_failures
from enten.parsers import parse_pytest_json
from enten.parsers.models import PytestResult
from enten.sandbox.models import ExecResult


def _pytest_report(
    nodeid: str,
    message: str,
    file: str | None = None,
    line: int | None = None,
) -> PytestResult:
    """Build a PytestResult from a minimal JSON report."""
    raw: dict[str, Any] = {
        "summary": {"passed": 0, "failed": 1, "total": 1, "duration": 0.1},
        "tests": [
            {
                "nodeid": nodeid,
                "outcome": "failed",
                "duration": 0.01,
                "lineno": line,
                "call": {"longrepr": message},
            }
        ],
    }
    if file is not None:
        raw["tests"][0]["filename"] = file
    return parse_pytest_json(json.dumps(raw))


class _FakeHandle:
    """Minimal sandbox handle that returns canned command output."""

    _files: dict[str, str]
    _blame: dict[tuple[str, int], str]

    def __init__(self, files: dict[str, str], blame: dict[tuple[str, int], str]) -> None:
        self._files = files
        self._blame = blame

    async def run(self, cmd: list[str], timeout_seconds: int = 120) -> ExecResult:
        _ = timeout_seconds
        if cmd[0] == "cat":
            path = cmd[1].removeprefix("/workspace/")
            content = self._files.get(path, "")
            if content or path in self._files:
                return ExecResult(exit_code=0, stdout=content, stderr="", duration_ms=1)
            return ExecResult(exit_code=1, stdout="", stderr="No such file", duration_ms=1)
        if cmd[0] == "git" and "blame" in cmd:
            path = cmd[-1].removeprefix("/workspace/")
            line = int(cmd[cmd.index("-L") + 1].split(",")[0])
            output = self._blame.get((path, line), "")
            return ExecResult(exit_code=0, stdout=output, stderr="", duration_ms=1)
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    async def upload(self, files: dict[str, str | bytes]) -> None:
        _ = files
        return None

    async def destroy(self) -> None:
        return None


@pytest.mark.asyncio
async def test_attribute_failure_maps_frame_to_symbol() -> None:
    """A traceback frame pointing inside a function yields that function's symbol."""
    source = "def helper():\n    x = 1\n    assert x == 2\n"
    report = _pytest_report(
        "src/app.py::test_helper",
        '  File "src/app.py", line 3, in helper\n    assert x == 2',
        file="src/app.py",
        line=5,
    )
    handle = _FakeHandle({"src/app.py": source}, {})

    attrs = await attribute_failures(report, handle)

    assert len(attrs) == 1
    attr = attrs[0]
    assert isinstance(attr, Attribution)
    assert attr.file == "src/app.py"
    assert attr.line == 3
    assert attr.symbol_name == "helper"
    assert attr.symbol_kind == "function"
    assert attr.parent_symbol is None
    assert attr.confidence == 1.0
    assert attr.low_confidence is False


@pytest.mark.asyncio
async def test_attribute_failure_populates_blame() -> None:
    """Stubbed git blame output is parsed into commit, author and message."""
    source = "class Calculator:\n    def add(self, a, b):\n        return a + b\n"
    report = _pytest_report(
        "src/calc.py::test_add",
        '  File "src/calc.py", line 3, in add\n    assert False',
        file="src/calc.py",
        line=4,
    )
    blame = (
        "deadbeef 3 3 1\n"
        "author Ada Lovelace\n"
        "author-mail <ada@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "summary implement add\n"
        "filename src/calc.py\n"
        "\treturn a + b\n"
    )
    handle = _FakeHandle({"src/calc.py": source}, {("src/calc.py", 3): blame})

    attrs = await attribute_failures(report, handle)

    assert len(attrs) == 1
    attr = attrs[0]
    assert attr.symbol_name == "add"
    assert attr.parent_symbol == "Calculator"
    assert attr.blame_commit == "deadbeef"
    assert attr.blame_author == "Ada Lovelace"
    assert attr.blame_message == "implement add"


@pytest.mark.asyncio
async def test_attribute_failure_low_confidence_for_unusable_traceback() -> None:
    """A traceback with no readable project file is flagged low-confidence."""
    report = _pytest_report(
        "test_demo.py::test_bad",
        """Traceback (most recent call last):
  File "<string>", line 1, in <module>
RuntimeError: boom""",
        file="test_demo.py",
        line=6,
    )
    handle = _FakeHandle({}, {})

    attrs = await attribute_failures(report, handle)

    assert len(attrs) == 1
    attr = attrs[0]
    assert attr.confidence < 0.3
    assert attr.low_confidence is True
