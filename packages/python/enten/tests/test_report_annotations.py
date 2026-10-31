"""Tests for evidence-backed annotations in the check report."""

from __future__ import annotations

from pathlib import Path

from enten.reporting.payload import build_report
from enten.review.diff import DiffIndex
from enten.review.run import (
    Finding,
    FindingEvidence,
    GeneratedFindings,
    Proposal,
    ReviewRequest,
    ReviewRun,
    VerificationEvidence,
)

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


def _run(tmp_path: Path, *, with_evidence: bool) -> ReviewRun:
    source = "import app\n\ndef test_add():\n    assert app.add(2, 3) == 5\n"
    evidence = (
        FindingEvidence(
            probe_command=("python", "-m", "pytest", "-q"),
            test_source=source,
            exit_code=1,
            output_excerpt="app.py:2: AssertionError",
            test_sha256="a" * 64,
        )
        if with_evidence
        else None
    )
    return ReviewRun(
        review_id="review-1",
        request=ReviewRequest(
            repository_id="public/repo",
            repository_full_name="public/repo",
            pr_number=1,
            head_sha="a" * 40,
            repository_path=tmp_path,
            verification_command=("true",),
            diff=DIFF,
        ),
        stage="persisted",
        proposal=Proposal(
            repository_path=tmp_path,
            files=("app.py",),
            verification_command=("true",),
            diff=DIFF,
            changed=DiffIndex.from_patch(DIFF),
        ),
        findings=GeneratedFindings(
            findings=(
                Finding(
                    category="bug",
                    severity="high",
                    message="Subtraction is used.",
                    file="app.py",
                    line=2,
                    evidence=evidence,
                ),
            )
        ),
        verification=VerificationEvidence(
            exit_codes=(0, 0, 0), command=("true",), log_paths=(), verdict="pass"
        ),
    )


def test_confirmed_finding_becomes_a_check_annotation(tmp_path: Path) -> None:
    payload = build_report(_run(tmp_path, with_evidence=True))

    assert len(payload.annotations) == 1
    annotation = payload.annotations[0]
    assert annotation.path == "app.py"
    assert annotation.start_line == 2
    assert annotation.annotation_level == "warning"
    assert "Reproduction for app.py:2" in payload.comment
    assert "pytest" in payload.comment


def test_finding_without_evidence_is_not_annotated(tmp_path: Path) -> None:
    payload = build_report(_run(tmp_path, with_evidence=False))

    assert payload.annotations == ()
    assert "app.py:2" in payload.comment
