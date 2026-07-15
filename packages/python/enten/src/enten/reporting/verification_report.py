from __future__ import annotations

import base64
import json
from typing import ClassVar, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from enten.failure_attribution import Attribution
from enten.github.client import GitHubClient
from enten.github.models import CheckRunOutput
from enten.llm.injection import validate_review_output
from enten.reporting.checks import MAX_SUMMARY_LENGTH, MAX_TITLE_LENGTH
from enten.reporting.comment import (
    BOT_MARKER,
    Finding,
    PRReview,
    render_pr_comment,
    sanitize_markdown_block,
    sanitize_markdown_inline,
)
from enten.test_gen import GeneratedTests, ReviewDraft
from enten.verification.verify import VerificationResult


class Patch(BaseModel):
    """Optional standalone patch content attached to a review."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    content: str = ""


class PRReviewRun(BaseModel):
    """All artifacts produced while reviewing and verifying a pull request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    review: PRReview
    draft: ReviewDraft
    patch: Patch | None = None
    tests: GeneratedTests | None = None
    verification: VerificationResult
    attributions: list[Attribution] = Field(default_factory=list)


class ChecksAPIOutput(TypedDict):
    """GitHub Checks API ``output`` object."""

    title: str
    summary: str


class ReportOutput(TypedDict):
    """Return value of :func:`render_report`."""

    checks_api_output: ChecksAPIOutput
    pr_comment_md: str


_CONCLUSION_MAP: dict[str, str] = {
    "pass": "success",
    "fail": "failure",
    "flaky": "neutral",
    "error": "failure",
}


def _truncate(text: str, max_length: int) -> str:
    """Truncate ``text`` to ``max_length``, preserving a trailing ellipsis."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _plain_text_for_validation(markdown: str) -> str:
    """Return a copy of ``markdown`` with backticks removed.

    ``render_pr_comment`` wraps ``file:line`` references in backticks, but
    T29's validator treats adjacent punctuation as part of the path.
    Stripping backticks before validation lets the regex match the actual
    file path while leaving the rendered comment untouched.
    """
    return markdown.replace("`", "")


def _available_files(review_run: PRReviewRun) -> set[str]:
    """Return the set of files that may be referenced by findings."""
    for source in (review_run.review.summary, review_run.review.evidence):
        if source and "available_files" in source:
            raw: object = source["available_files"]
            if isinstance(raw, list):
                result: set[str] = set()
                for path in raw:
                    result.add(str(path))
                return result
    return set()


def _has_valid_file_line(finding: Finding, available: set[str]) -> bool:
    """A finding is verifiable when it points to a known file and a real line."""
    return bool(finding.file) and finding.file in available and finding.line > 0


def _filter_findings(findings: list[Finding], available: set[str]) -> list[Finding]:
    """Drop findings that lack a valid ``file:line`` reference."""
    return [f for f in findings if _has_valid_file_line(f, available)]


def _suggested_patch(review_run: PRReviewRun) -> str:
    """Prefer the review's patch, falling back to the standalone patch object."""
    if review_run.review.suggested_patch:
        return review_run.review.suggested_patch
    if review_run.patch is not None:
        return review_run.patch.content
    return ""


def _suggested_tests(review_run: PRReviewRun) -> list[str]:
    """Prefer the review's test list, falling back to generated test metadata."""
    if review_run.review.suggested_tests:
        return review_run.review.suggested_tests
    if review_run.tests is not None:
        return [f"{t.file_path}: {t.rationale}" for t in review_run.tests.tests]
    return []


def _build_evidence(review_run: PRReviewRun, suppressed_count: int) -> dict[str, object]:
    """Assemble the evidence object embedded in the comment and checks payload."""
    evidence: dict[str, object] = dict(review_run.review.evidence or {})
    evidence["verification_outcome"] = review_run.verification.outcome
    evidence["verification_exit_codes"] = review_run.verification.exit_codes
    evidence["verification_flaky"] = review_run.verification.flaky
    evidence["attributions"] = [a.model_dump() for a in review_run.attributions]
    evidence["suppressed_findings_count"] = suppressed_count
    return evidence


def _no_verifiable_findings_markdown(evidence: dict[str, object]) -> str:
    """Fallback comment when every finding was stripped for lacking file:line."""
    json_text = json.dumps(evidence, indent=2, default=str)
    b64 = base64.b64encode(json_text.encode("utf-8")).decode("ascii")
    return (
        f"{BOT_MARKER}\n\n"
        "## No verifiable findings\n\n"
        "All findings lacked a valid `file:line` reference. "
        f"[Download evidence.json](data:application/json;base64,{b64})\n\n"
        "<details>\n"
        "<summary>Evidence JSON</summary>\n\n"
        "```json\n"
        f"{sanitize_markdown_block(_truncate(json_text, 4000))}\n"
        "```\n\n"
        "</details>\n"
    )


def render_report(review_run: PRReviewRun) -> ReportOutput:
    """Render a verification report for the Checks API and a PR comment.

    Returns a dict with two keys:

    - ``checks_api_output``: ``{"title": str, "summary": str}`` ready for
      :func:`enten.reporting.checks.post_check_run` or
      :meth:`enten.github.client.GitHubClient.create_check_run`.
    - ``pr_comment_md``: GitHub-flavored markdown comment text, always
      containing the ``<!-- enten-bot -->`` marker.

    Findings without a valid ``file:line`` reference are stripped. If no
    verifiable references remain, the comment is replaced with a
    "no verifiable findings" notice and a link to the evidence JSON.
    """
    available = _available_files(review_run)

    static = _filter_findings(review_run.review.static_findings, available)
    security = _filter_findings(review_run.review.security_findings, available)
    suppressed = [
        f
        for f in review_run.review.static_findings + review_run.review.security_findings
        if not _has_valid_file_line(f, available)
    ]

    evidence = _build_evidence(review_run, len(suppressed))

    review_for_comment = PRReview(
        summary=dict(review_run.review.summary or {}),
        static_findings=static,
        security_findings=security,
        suggested_tests=_suggested_tests(review_run),
        suggested_patch=_suggested_patch(review_run),
        verdict=review_run.verification.outcome,
        evidence=evidence,
    )

    markdown = render_pr_comment(review_for_comment)
    rendered_available = [sanitize_markdown_inline(path) for path in available]
    validated = validate_review_output(_plain_text_for_validation(markdown), rendered_available)

    if not validated.verified:
        markdown = _no_verifiable_findings_markdown(evidence)

    title = _truncate(f"enten review: {review_for_comment.verdict}", MAX_TITLE_LENGTH)
    summary = _truncate(markdown, MAX_SUMMARY_LENGTH)

    return {
        "checks_api_output": {"title": title, "summary": summary},
        "pr_comment_md": markdown,
    }


async def post_report(client: GitHubClient, review_run: PRReviewRun) -> None:
    """Post the verification report as a check run and an idempotent PR comment.

    ``review_run.review.summary`` must contain ``owner``, ``repo``,
    ``sha`` (strings) and ``pr_number`` (int).

    The check run is created first; the PR comment is posted second. Both
    use the same rendered output so the UI stays consistent.
    """
    summary = review_run.review.summary or {}
    owner = summary.get("owner")
    repo = summary.get("repo")
    sha = summary.get("sha")
    pr_number = summary.get("pr_number")

    if not all(isinstance(value, str) for value in (owner, repo, sha)):
        raise ValueError("review.summary must contain owner, repo, and sha as strings")
    if not isinstance(pr_number, int):
        raise ValueError("review.summary must contain pr_number as an int")

    report = render_report(review_run)
    conclusion = _CONCLUSION_MAP[review_run.verification.outcome]

    output = CheckRunOutput(**report["checks_api_output"])
    _ = await client.create_check_run(
        str(owner),
        str(repo),
        str(sha),
        conclusion,
        output,
    )
    _ = await client.post_pr_comment(
        str(owner),
        str(repo),
        int(pr_number),
        report["pr_comment_md"],
    )
