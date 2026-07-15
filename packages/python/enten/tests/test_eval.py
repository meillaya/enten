"""Tests for the eval harness (T37)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from enten.eval import EvalError, EvalReport, run_eval, write_baseline

CHECKER = Path(__file__).resolve().parents[4] / "scripts" / "eval-threshold-check.py"


def _make_fixture(
    root: Path,
    name: str,
    *,
    kind: str,
    expected_findings: list[dict[str, Any]],
    stub_findings: list[dict[str, Any]] | None = None,
) -> Path:
    """Create a synthetic fixture directory under ``root``."""
    fixture_dir = root / kind / name
    fixture_dir.mkdir(parents=True)

    diff_text = (
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-print('old')\n"
        "+print('new')\n"
    )
    pr = {
        "id": hash(name) % 2_147_483_647,
        "number": hash(name) % 10_000,
        "title": f"Fixture {name}",
        "body": f"Synthetic fixture of kind {kind}.",
        "state": "open",
        "head_sha": "deadbeef" * 5,
        "base_sha": "feedface" * 5,
        "user": {"login": "test-author"},
        "head": {"ref": f"fixture/{name}", "sha": "deadbeef" * 5},
        "base": {"ref": "main", "sha": "feedface" * 5},
        "files": {"src/example.py": diff_text},
    }

    (fixture_dir / "pr.json").write_text(json.dumps(pr), encoding="utf-8")
    files = cast(dict[str, str], pr["files"])
    (fixture_dir / "diff.patch").write_text(files["src/example.py"], encoding="utf-8")
    (fixture_dir / "expected_findings.json").write_text(
        json.dumps(expected_findings), encoding="utf-8"
    )
    (fixture_dir / "meta.json").write_text(
        json.dumps({"kind": kind, "source": "synthetic"}), encoding="utf-8"
    )
    if stub_findings is not None:
        (fixture_dir / "stub_findings.json").write_text(json.dumps(stub_findings), encoding="utf-8")
    referenced = expected_findings + (stub_findings or [])
    required_lines: dict[str, int] = {}
    for finding in referenced:
        path = cast(str, finding["file"])
        line = cast(int, finding["line"])
        required_lines[path] = max(required_lines.get(path, 0), line)
    for relative, line_count in required_lines.items():
        source = fixture_dir / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "".join(f"fixture line {number}\n" for number in range(1, line_count + 1)),
            encoding="utf-8",
        )
    return fixture_dir


@pytest.mark.asyncio
async def test_run_eval_computes_metrics_and_baseline_diff(tmp_path: Path) -> None:
    """Three synthetic fixtures produce expected metrics and a baseline diff."""
    corpus = tmp_path / "corpus"

    bug_off_by_one = {
        "category": "bug",
        "severity": "high",
        "message": "Off-by-one",
        "file": "src/loop.py",
        "line": 10,
    }
    bug_null_deref = {
        "category": "bug",
        "severity": "medium",
        "message": "Null deref",
        "file": "src/core.py",
        "line": 20,
    }
    bug_ghost = {
        "category": "bug",
        "severity": "medium",
        "message": "Ghost bug",
        "file": "src/ghost.py",
        "line": 99,
    }
    security_sql = {
        "category": "security",
        "severity": "critical",
        "message": "SQL injection",
        "file": "src/db.py",
        "line": 5,
    }

    # bug: 2 expected, bot returns 1 correct + 1 hallucination -> precision 0.5, recall 0.5
    _make_fixture(
        corpus,
        "bug_off_by_one",
        kind="bug",
        expected_findings=[bug_off_by_one, bug_null_deref],
        stub_findings=[bug_off_by_one, bug_ghost],
    )

    # security: 1 expected, bot returns 1 correct -> precision/recall 1.0
    _make_fixture(
        corpus,
        "security_sql",
        kind="security",
        expected_findings=[security_sql],
        stub_findings=[security_sql],
    )

    # no_false_positive: 0 expected, bot returns 0 -> FPR 0
    _make_fixture(
        corpus,
        "clean_refactor",
        kind="no_false_positive",
        expected_findings=[],
        stub_findings=[],
    )

    report = await run_eval(str(corpus), stub_llm=True)

    assert isinstance(report, EvalReport)
    assert report.n_fixtures == 3
    assert "bug" in report.by_kind
    assert "security" in report.by_kind
    assert "no_false_positive" in report.by_kind

    bug = report.by_kind["bug"]
    assert bug.n_correct == 1
    assert bug.n_missed == 1
    assert bug.n_hallucinated == 1
    assert bug.precision == pytest.approx(0.5)
    assert bug.recall == pytest.approx(0.5)
    assert bug.false_positive_rate == pytest.approx(0.5)

    security = report.by_kind["security"]
    assert security.n_correct == 1
    assert security.n_missed == 0
    assert security.n_hallucinated == 0
    assert security.precision == pytest.approx(1.0)
    assert security.recall == pytest.approx(1.0)
    assert security.false_positive_rate == pytest.approx(0.0)

    clean = report.by_kind["no_false_positive"]
    assert clean.n_correct == 0
    assert clean.n_missed == 0
    assert clean.n_hallucinated == 0
    assert clean.false_positive_rate == 0.0

    assert report.determinism.get("passed") is True
    assert report.cost["fixture_count"] == 3
    assert report.latency["total_ms"] >= 0

    # Baseline diff: write a baseline, run again, and assert diff reports no change.
    baseline_path = tmp_path / "eval-baseline.json"
    write_baseline(report, baseline_path)

    second_report = await run_eval(str(corpus), stub_llm=True, baseline_path=str(baseline_path))
    assert second_report.baseline is not None
    assert second_report.baseline["changed"] is False
    assert second_report.baseline["previous"] is not None


@pytest.mark.asyncio
async def test_run_eval_determinism_check_is_byte_identical(tmp_path: Path) -> None:
    """The bundled determinism fixture yields byte-identical verify JSON."""
    corpus = tmp_path / "corpus"
    _make_fixture(
        corpus,
        "clean_refactor",
        kind="no_false_positive",
        expected_findings=[],
        stub_findings=[],
    )

    report = await run_eval(str(corpus), stub_llm=True)

    assert report.determinism["passed"] is True
    first = report.determinism["first"]
    second = report.determinism["second"]
    assert first == second
    assert first["outcome"] == "pass"
    assert second["outcome"] == "pass"
    assert first["summary"]["total"] == 1


@pytest.mark.asyncio
async def test_run_eval_empty_corpus_raises_eval_error(tmp_path: Path) -> None:
    """An empty corpus directory raises :class:`EvalError`."""
    empty_corpus = tmp_path / "empty"
    empty_corpus.mkdir()

    with pytest.raises(EvalError, match="corpus has 0 fixtures"):
        await run_eval(str(empty_corpus), stub_llm=True)


@pytest.mark.asyncio
async def test_stub_eval_requires_predictions_instead_of_using_gold(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _make_fixture(
        corpus,
        "missing_prediction",
        kind="bug",
        expected_findings=[
            {
                "category": "bug",
                "severity": "high",
                "message": "Expected bug",
                "file": "src/example.py",
                "line": 1,
            }
        ],
    )

    with pytest.raises(EvalError, match="stub predictions not found"):
        await run_eval(str(corpus), stub_llm=True)


@pytest.mark.asyncio
async def test_report_exposes_checker_aggregate_schema(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    finding = {
        "category": "bug",
        "severity": "high",
        "message": "Expected bug",
        "file": "src/example.py",
        "line": 1,
    }
    _make_fixture(
        corpus,
        "predicted_bug",
        kind="bug",
        expected_findings=[finding],
        stub_findings=[finding],
    )

    report = await run_eval(str(corpus), stub_llm=True)

    dumped = report.model_dump()
    assert dumped["aggregate"] == {
        "precision": 1.0,
        "recall": 1.0,
        "false_positive_rate": 0.0,
        "count": 1,
        "n_correct": 1,
        "n_missed": 0,
        "n_hallucinated": 0,
    }


@pytest.mark.parametrize(
    ("report", "expected_exit"),
    [
        (
            {
                "aggregate": {
                    "precision": 1.0,
                    "recall": 1.0,
                    "false_positive_rate": 0.0,
                }
            },
            0,
        ),
        (
            {
                "aggregate": {
                    "precision": 0.0,
                    "recall": 0.0,
                    "false_positive_rate": 1.0,
                }
            },
            1,
        ),
        ({"by_kind": {}}, 2),
    ],
)
def test_eval_threshold_checker_exit_contract(
    tmp_path: Path,
    report: dict[str, object],
    expected_exit: int,
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(report_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_exit, result.stdout + result.stderr
