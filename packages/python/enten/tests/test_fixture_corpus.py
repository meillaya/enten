"""Corpus-level checks for the synthetic PR fixture set.

Validates that every fixture directory contains the required files,
that the total fixture count meets the expansion target, and that no
real secrets are present in the fixture corpus.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "prs"
REPO_ROOT = FIXTURE_ROOT.parents[2]

REQUIRED_FILES = ("pr.json", "diff.patch", "expected_findings.json", "meta.json")


def _discover_fixtures() -> list[Path]:
    """Return every leaf directory that contains at least one required file."""
    return [
        path
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_dir() and any((path / name).exists() for name in REQUIRED_FILES)
    ]


@pytest.fixture(scope="session")
def fixtures() -> list[Path]:
    return _discover_fixtures()


def test_fixture_files_are_complete(fixtures: list[Path]) -> None:
    """Every fixture directory must contain all four required files."""
    for fixture in fixtures:
        for name in REQUIRED_FILES:
            assert (fixture / name).is_file(), f"Missing {name} in {fixture}"


def test_fixture_pr_schema(fixtures: list[Path]) -> None:
    """pr.json must contain the fields consumed by the review pipeline."""
    required_fields = {
        "id",
        "number",
        "title",
        "body",
        "state",
        "head_sha",
        "base_sha",
        "user",
        "head",
        "base",
    }
    for fixture in fixtures:
        pr = json.loads((fixture / "pr.json").read_text())
        missing = required_fields - set(pr.keys())
        assert not missing, f"{fixture / 'pr.json'} missing fields: {missing}"
        assert "login" in pr["user"], f"{fixture} user.login missing"
        assert "ref" in pr["head"] and "sha" in pr["head"], f"{fixture} head ref/sha missing"
        assert "ref" in pr["base"] and "sha" in pr["base"], f"{fixture} base ref/sha missing"


def test_fixture_meta_schema(fixtures: list[Path]) -> None:
    """meta.json must declare the fixture kind and description."""
    for fixture in fixtures:
        meta = json.loads((fixture / "meta.json").read_text())
        assert "kind" in meta, f"{fixture / 'meta.json'} missing kind"
        assert "description" in meta, f"{fixture / 'meta.json'} missing description"


def test_fixture_expected_findings_shape(fixtures: list[Path]) -> None:
    """expected_findings.json must be a list of findings with required keys."""
    required_finding_fields = {"category", "severity", "file", "line", "message"}
    for fixture in fixtures:
        findings = json.loads((fixture / "expected_findings.json").read_text())
        assert isinstance(findings, list), f"{fixture} expected_findings is not a list"
        for finding in findings:
            missing = required_finding_fields - set(finding.keys())
            assert not missing, f"{fixture} finding missing fields: {missing}"


def test_fixture_corpus_count(fixtures: list[Path]) -> None:
    """The expanded corpus must contain at least ten fixtures."""
    assert len(fixtures) >= 10, f"Expected >= 10 fixtures, found {len(fixtures)}"


def test_no_real_secrets_in_fixtures() -> None:
    """gitleaks must report zero findings in the fixture corpus."""
    report_path = Path("/tmp/gl.json")
    result = subprocess.run(
        [
            "gitleaks",
            "detect",
            "--no-git",
            "--source",
            str(REPO_ROOT / "tests" / "fixtures"),
            "--report-path",
            str(report_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"gitleaks reported findings in fixtures:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
