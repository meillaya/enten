"""Tests for the verification report generator."""

from __future__ import annotations

from typing import Literal, cast

import pytest

from enten.failure_attribution import Attribution
from enten.github.client import GitHubClient
from enten.reporting.comment import BOT_MARKER, Finding, PRReview
from enten.reporting.verification_report import Patch, PRReviewRun, post_report, render_report
from enten.test_gen import GeneratedTest, GeneratedTests, ReviewDraft
from enten.verification.flaky import RunSummary
from enten.verification.verify import RunEvidence, VerificationResult


def _verification(
    outcome: Literal["pass", "flaky", "fail", "error"] = "fail",
) -> VerificationResult:
    """Build a minimal VerificationResult for tests."""
    return VerificationResult(
        outcome=outcome,
        runs=[],
        exit_codes=[1],
        flaky=False,
        sandbox_ids=["sandbox-1"],
        log_paths=["/tmp/pytest-report.xml"],
    )


def test_render_report_strips_findings_without_file_line() -> None:
    """``validate_review_output`` enforcement drops findings lacking a valid file:line."""
    review = PRReview(
        summary={"available_files": ["src/app.py", "src/utils.py"]},
        static_findings=[
            Finding(
                category="security",
                severity="high",
                message="Use of eval() detected",
                file="src/app.py",
                line=42,
            ),
            Finding(
                category="style",
                severity="low",
                message="Something vague without a real location",
                file="src/unknown.py",
                line=1,
            ),
        ],
        verdict="fail",
    )
    run = PRReviewRun(
        review=review,
        draft=ReviewDraft(content=""),
        verification=_verification("fail"),
        attributions=[],
    )

    result = render_report(run)
    markdown = result["pr_comment_md"]

    assert "src/app.py:42" in markdown
    assert "Use of eval() detected" in markdown
    assert "Something vague" not in markdown
    assert "src/unknown.py" not in markdown


def test_render_report_end_to_end_sections() -> None:
    """A fully populated review run renders every expected comment section."""
    review = PRReview(
        summary={
            "available_files": ["src/app.py"],
            "owner": "acme",
            "repo": "widgets",
            "sha": "abc1234",
            "pr_number": 7,
            "author": "test-author",
        },
        static_findings=[
            Finding(
                category="python.lang.security.audit",
                severity="high",
                message="Use of eval() detected",
                file="src/app.py",
                line=2,
            )
        ],
        security_findings=[
            Finding(
                category="secrets",
                severity="critical",
                message="Hardcoded API key",
                file="src/app.py",
                line=3,
            )
        ],
        suggested_tests=["Add regression test for eval path"],
        suggested_patch="@@ -1,3 +1,3 @@\n- eval(x)\n+ ast.literal_eval(x)",
        verdict="fail",
    )
    tests = GeneratedTests(
        language="python",
        tests=[
            GeneratedTest(
                file_path="tests/test_app.py",
                content="def test_eval(): ...",
                sha256="deadbeef",
                rationale="covers the eval branch",
            )
        ],
    )
    patch = Patch(content="suggested patch content")
    verification = VerificationResult(
        outcome="fail",
        runs=[
            RunEvidence(
                exit_code=1,
                summary=RunSummary(failed=1, passed=0),
                duration_ms=1234,
                sandbox_id="sandbox-1",
                command=["pytest", "tests/test_app.py"],
                log_paths=["/tmp/pytest-report.xml"],
            )
        ],
        exit_codes=[1],
        flaky=False,
        sandbox_ids=["sandbox-1"],
        log_paths=["/tmp/pytest-report.xml"],
    )
    attributions = [
        Attribution(
            failure_id="tests/test_app.py::test_eval",
            file="src/app.py",
            line=2,
            symbol_kind="function",
            symbol_name="process",
            parent_symbol=None,
            blame_commit="a1b2c3d",
            blame_author="dev@example.com",
            blame_message="Add eval helper",
            confidence=0.9,
        )
    ]

    run = PRReviewRun(
        review=review,
        draft=ReviewDraft(content="Review draft summary"),
        patch=patch,
        tests=tests,
        verification=verification,
        attributions=attributions,
    )

    result = render_report(run)
    markdown = result["pr_comment_md"]

    assert BOT_MARKER in markdown
    assert "test-author" in markdown
    assert "abc1234" in markdown
    assert "Static analysis" in markdown
    assert "Security findings" in markdown
    assert "Suggested tests" in markdown
    assert "Suggested patch" in markdown
    assert "Deterministic verdict" in markdown
    assert "src/app.py:2" in markdown
    assert "src/app.py:3" in markdown
    assert "fail" in markdown


def test_render_report_checks_api_output_length_limits() -> None:
    """Checks API title and summary respect GitHub's hard length limits."""
    huge_message = "x" * 100_000
    review = PRReview(
        summary={"available_files": ["src/app.py"]},
        static_findings=[
            Finding(
                category="security",
                severity="high",
                message=huge_message,
                file="src/app.py",
                line=1,
            )
        ],
        verdict="fail",
    )
    run = PRReviewRun(
        review=review,
        draft=ReviewDraft(content=""),
        verification=_verification("fail"),
        attributions=[],
    )

    result = render_report(run)
    output = result["checks_api_output"]

    assert len(output["title"]) <= 80
    assert len(output["summary"]) <= 65_535
    assert output["title"].startswith("enten review:")


@pytest.mark.anyio
async def test_post_report_creates_check_run_then_comment() -> None:
    """post_report calls create_check_run before post_pr_comment."""
    review = PRReview(
        summary={
            "available_files": ["src/app.py"],
            "owner": "acme",
            "repo": "widgets",
            "sha": "abc1234",
            "pr_number": 7,
        },
        static_findings=[
            Finding(
                category="security",
                severity="high",
                message="eval",
                file="src/app.py",
                line=1,
            )
        ],
        verdict="fail",
    )
    run = PRReviewRun(
        review=review,
        draft=ReviewDraft(content=""),
        verification=_verification("fail"),
        attributions=[],
    )

    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeClient:
        async def create_check_run(
            self, owner: str, repo: str, sha: str, conclusion: str, output: object
        ) -> dict[str, object]:
            calls.append(("create_check_run", (owner, repo, sha, conclusion, output)))
            return {"id": 1}

        async def post_pr_comment(
            self, owner: str, repo: str, number: int, body: str
        ) -> dict[str, object]:
            calls.append(("post_pr_comment", (owner, repo, number, body)))
            return {"id": 2}

    client = cast(GitHubClient, cast(object, FakeClient()))
    await post_report(client, run)

    assert len(calls) == 2
    assert calls[0][0] == "create_check_run"
    assert calls[1][0] == "post_pr_comment"
    body = cast(str, calls[1][1][-1])
    assert BOT_MARKER in body
