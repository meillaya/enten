"""Tests for the PR comment markdown formatter and Checks API reporter."""

from __future__ import annotations

import pytest

from enten.reporting.checks import (
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    post_check_run,
    update_check_run,
)
from enten.reporting.comment import (
    BOT_MARKER,
    Finding,
    PRReview,
    render_pr_comment,
)


def test_render_pr_comment_full_review() -> None:
    """A full review fixture renders every expected markdown section."""
    review = PRReview(
        summary={
            "bot_name": "enten-bot",
            "author": "test-author",
            "sha": "abc1234",
        },
        static_findings=[
            Finding(
                category="python.lang.security.audit",
                severity="high",
                message="Use of eval() detected",
                file="src/app.py",
                line=42,
            ),
            Finding(
                category="python.lang.best-practice",
                severity="medium",
                message="Unused import",
                file="src/utils.py",
                line=7,
            ),
        ],
        security_findings=[
            Finding(
                category="secrets",
                severity="critical",
                message="Hardcoded API key",
                file="config.yml",
                line=12,
            )
        ],
        suggested_tests=["Add regression test for eval path", "Verify API key redaction"],
        suggested_patch="@@ -1,3 +1,3 @@\n- eval(x)\n+ ast.literal_eval(x)",
        verdict="fail",
        evidence={"files_changed": 3, "findings": 3},
    )

    markdown = render_pr_comment(review)

    assert BOT_MARKER in markdown
    assert "enten-bot" in markdown
    assert "test-author" in markdown
    assert "abc1234" in markdown
    assert "Static analysis" in markdown
    assert "Security findings" in markdown
    assert "Suggested tests" in markdown
    assert "Suggested patch" in markdown
    assert "Deterministic verdict" in markdown
    assert "src/app.py:42" in markdown
    assert "config.yml:12" in markdown
    assert "eval()" in markdown
    assert "Hardcoded API key" in markdown
    assert "fail" in markdown


def test_render_pr_comment_sanitizes_script_tag() -> None:
    """Dangerous HTML in finding messages is escaped or stripped."""
    review = PRReview(
        summary={},
        static_findings=[
            Finding(
                category="xss",
                severity="high",
                message="<script>alert(1)</script> badness",
                file="src/xss.py",
                line=1,
            )
        ],
        verdict="fail",
    )

    markdown = render_pr_comment(review)

    assert "<script>" not in markdown
    assert "<iframe>" not in markdown
    assert "badness" in markdown


def test_render_pr_comment_flags_missing_location() -> None:
    """Findings without file:line are rendered with an unknown location label."""
    review = PRReview(
        summary={},
        static_findings=[
            Finding(
                category="generic",
                severity="low",
                message="Something vague",
                file="",
                line=0,
            )
        ],
        verdict="pass",
    )

    markdown = render_pr_comment(review)

    assert "unknown location" in markdown


def test_render_pr_comment_truncates_large_patch() -> None:
    """Patches exceeding the inline size limit are truncated."""
    review = PRReview(
        summary={},
        suggested_patch="+ line\n" * 10_000,
        verdict="pass",
    )

    markdown = render_pr_comment(review)

    assert "patch truncated due to size" in markdown
    assert len(markdown) < len(review.suggested_patch)


@pytest.mark.anyio
async def test_post_check_run_validates_title_length() -> None:
    """post_check_run rejects titles longer than GitHub's limit."""
    with pytest.raises(ValueError, match="output.title exceeds"):
        await post_check_run(
            client=None,  # type: ignore[arg-type]
            owner="owner",
            repo="repo",
            sha="sha",
            conclusion="failure",
            output={"title": "x" * (MAX_TITLE_LENGTH + 1), "summary": "summary"},
        )


@pytest.mark.anyio
async def test_update_check_run_validates_summary_length() -> None:
    """update_check_run rejects summaries longer than GitHub's limit."""
    with pytest.raises(ValueError, match="output.summary exceeds"):
        await update_check_run(
            client=None,  # type: ignore[arg-type]
            owner="owner",
            repo="repo",
            check_run_id="123",
            conclusion="failure",
            output={"title": "title", "summary": "x" * (MAX_SUMMARY_LENGTH + 1)},
        )
