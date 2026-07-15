from __future__ import annotations

from hashlib import sha256
from typing import Final

from enten.reporting.comment import sanitize_markdown_inline
from enten.review.run import ReportPayload, ReviewRun

_COMMENT_MARKER_DOMAIN: Final = b"enten-pr-review-comment-v1\x00"


def comment_marker(head_sha: str) -> str:
    """Return an injection-safe marker scoped to one reviewed commit."""
    digest = sha256(_COMMENT_MARKER_DOMAIN + head_sha.encode()).hexdigest()
    return f"<!-- enten-bot:review:{digest} -->"


def build_report(run: ReviewRun) -> ReportPayload:
    if run.findings is None or run.verification is None or run.proposal is None:
        raise ValueError("reporting requires findings, proposal, and verification")
    available = set(run.proposal.files)
    anchored = [finding for finding in run.findings.findings if finding.file in available]
    evidence_lines: list[str] = []
    for finding in anchored:
        file = sanitize_markdown_inline(finding.file)
        message = sanitize_markdown_inline(finding.message)
        evidence_lines.append(f"- `{file}:{finding.line}` [{finding.severity}] {message}")
    verdict = run.verification.verdict
    marker = comment_marker(run.request.head_sha)
    infrastructure_detail = run.verification.infrastructure_detail
    infrastructure_lines = (
        []
        if infrastructure_detail is None
        else [f"Infrastructure error: {sanitize_markdown_inline(infrastructure_detail)}"]
    )
    body = "\n".join(
        [
            marker,
            f"## enten review: {verdict}",
            f"Reviewed commit: `{sanitize_markdown_inline(run.request.head_sha)}`",
            f"Verification exits: `{list(run.verification.exit_codes)}`",
            *infrastructure_lines,
            "",
            "### Findings",
            *(evidence_lines or ["No verifiable findings."]),
        ]
    )
    comment = body[:65_535]
    owner, repo = _repository_parts(run.request.repository_full_name)
    conclusion = "success" if verdict == "pass" else "neutral" if verdict == "flaky" else "failure"
    return ReportPayload(
        owner=owner,
        repo=repo,
        pr_number=run.request.pr_number,
        head_sha=run.request.head_sha,
        conclusion=conclusion,
        title=f"enten review: {verdict}"[:80],
        summary=comment,
        comment=comment,
    )


def _repository_parts(full_name: str | None) -> tuple[str | None, str | None]:
    if full_name is None:
        return None, None
    parts = full_name.split("/", maxsplit=1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


__all__ = ["build_report", "comment_marker"]
