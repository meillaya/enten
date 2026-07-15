from __future__ import annotations

import html

from enten.review.run import ReportPayload, ReviewRun


def build_report(run: ReviewRun) -> ReportPayload:
    if run.findings is None or run.verification is None or run.proposal is None:
        raise ValueError("reporting requires findings, proposal, and verification")
    available = set(run.proposal.files)
    anchored = [finding for finding in run.findings.findings if finding.file in available]
    evidence_lines = [
        f"- `{finding.file}:{finding.line}` [{finding.severity}] "
        f"{html.escape(finding.message).replace(chr(10), ' ')}"
        for finding in anchored
    ]
    verdict = run.verification.verdict
    body = "\n".join(
        [
            "<!-- enten-bot -->",
            f"## enten review: {verdict}",
            f"Verification exits: `{list(run.verification.exit_codes)}`",
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


__all__ = ["build_report"]
