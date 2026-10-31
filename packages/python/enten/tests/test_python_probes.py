"""Tests for Python probe policy and execution."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from enten.review.diff import DiffIndex
from enten.review.python_probes import (
    LocalProbeRunner,
    build_findings,
    classify_probe,
    validate_probe_source,
)
from enten.review.run import Hypothesis, HypothesisSet, Proposal, ReviewRequest

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


def _hypothesis(source: str) -> Hypothesis:
    return Hypothesis(
        id="hyp-1",
        category="bug",
        severity="high",
        message="Subtraction is used instead of addition.",
        file="app.py",
        line=2,
        rationale="The changed expression is arithmetic.",
        test_source=source,
        test_sha256=hashlib.sha256(source.encode()).hexdigest(),
    )


def test_diff_index_tracks_added_lines() -> None:
    index = DiffIndex.from_patch(DIFF)

    assert index.files == ("app.py",)
    assert index.added_lines["app.py"] == (2,)
    assert index.contains_added("app.py", 2)
    assert not index.contains_added("app.py", 1)


def test_probe_policy_rejects_trivial_and_missing_target() -> None:
    assert validate_probe_source("def test_x():\n    assert False\n", "app.py") is not None
    assert validate_probe_source("def test_x():\n    assert 1 == 1\n", "app.py") is not None


def test_probe_policy_accepts_a_targeted_test() -> None:
    source = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"
    assert validate_probe_source(source, "app.py") is None


def test_classify_probe_requires_target_in_failure_output() -> None:
    hypothesis = _hypothesis("import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n")

    confirmed = classify_probe(
        hypothesis,
        exit_code=1,
        output="app.py:2: AssertionError\nassert (2 - 3) == 5",
        duration_ms=10,
    )
    unrelated_hypothesis = _hypothesis("def test_x():\n    assert 1 == 2\n")
    unrelated = classify_probe(
        unrelated_hypothesis,
        exit_code=1,
        output="test.py:2: AssertionError",
        duration_ms=10,
    )

    assert confirmed.status == "confirmed"
    assert unrelated.status == "rejected"


async def test_local_probe_runner_confirms_a_real_bug(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    source = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"
    hypothesis = _hypothesis(source)
    proposal = Proposal(
        repository_path=repository,
        files=("app.py",),
        verification_command=("true",),
        diff=DIFF,
        changed=DiffIndex.from_patch(DIFF),
    )

    evidence = await LocalProbeRunner().run(
        HypothesisSet(hypotheses=(hypothesis,)),
        proposal,
    )

    assert len(evidence.results) == 1
    assert evidence.results[0].status == "confirmed"
    assert evidence.results[0].exit_code == 1
    findings = build_findings(HypothesisSet(hypotheses=(hypothesis,)), evidence)
    assert len(findings.findings) == 1
    assert findings.findings[0].evidence is not None
    assert not (repository / ".enten-probes").exists()


async def test_local_probe_runner_rejects_a_passing_test(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    source = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"
    hypothesis = _hypothesis(source)
    proposal = Proposal(
        repository_path=repository,
        files=("app.py",),
        verification_command=("true",),
    )

    evidence = await LocalProbeRunner().run(
        HypothesisSet(hypotheses=(hypothesis,)),
        proposal,
    )

    assert evidence.results[0].status == "rejected"


def test_review_request_rejects_oversized_diff() -> None:
    with pytest.raises(ValidationError):
        ReviewRequest(
            repository_id="local",
            pr_number=1,
            head_sha="a" * 40,
            repository_path=Path("."),
            verification_command=("true",),
            diff="x" * (64 * 1024 + 1),
        )
