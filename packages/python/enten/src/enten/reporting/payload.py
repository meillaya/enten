from __future__ import annotations

from hashlib import sha256
from typing import Final

from enten.reporting.comment import sanitize_markdown_block, sanitize_markdown_inline
from enten.review.run import Finding, ReportAnnotation, ReportPayload, ReviewRun

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
    annotations = _annotations(anchored, run)
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
            *_evidence_blocks(anchored),
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
        annotations=annotations,
    )


def _annotations(findings: list[Finding], run: ReviewRun) -> tuple[ReportAnnotation, ...]:
    annotations: list[ReportAnnotation] = []
    for finding in findings:
        if finding.evidence is None:
            continue
        if not run.proposal or not run.proposal.changed.contains_added(finding.file, finding.line):
            continue
        level = (
            "warning"
            if finding.severity in {"critical", "high", "medium"}
            else "notice"
        )
        annotations.append(
            ReportAnnotation(
                path=finding.file,
                start_line=finding.line,
                end_line=finding.line,
                annotation_level=level,
                title=finding.category[:80],
                message=finding.message[:1_000],
            )
        )
        if len(annotations) == 50:
            break
    return tuple(annotations)


def _evidence_blocks(findings: list[Finding]) -> list[str]:
    blocks: list[str] = []
    for finding in findings:
        evidence = finding.evidence
        if evidence is None:
            continue
        command = " ".join(evidence.probe_command)
        test = evidence.test_source[:4_000]
        output = evidence.output_excerpt[:1_500]
        blocks.extend(
            [
                "",
                f"<details><summary>Reproduction for {sanitize_markdown_inline(finding.file)}:"
                f"{finding.line}</summary>",
                "",
                f"`{sanitize_markdown_inline(command)}` exited `{evidence.exit_code}`.",
                "",
                "```python",
                sanitize_markdown_block(test),
                "```",
                "",
                "```text",
                sanitize_markdown_block(output),
                "```",
                "",
                "</details>",
            ]
        )
    return blocks


def _repository_parts(full_name: str | None) -> tuple[str | None, str | None]:
    if full_name is None:
        return None, None
    parts = full_name.split("/", maxsplit=1)
    if len(parts) != 2:
        return None, None
    return parts[0], parts[1]


__all__ = ["build_report", "comment_marker"]
