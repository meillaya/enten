"""Findings generator backed by a tool-calling language model."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from enten.review.generator import generate_review
from enten.review.run import Finding, GeneratedFindings, Proposal

MAX_EVIDENCE_BYTES = 60_000
MAX_EVIDENCE_FILES = 60


class LLMFindingsGenerator:
    """Generate bounded findings from repository evidence with an LLM."""

    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client

    async def generate(self, proposal: Proposal) -> GeneratedFindings:
        evidence = _repository_evidence(proposal)
        draft = await generate_review(
            {
                "number": proposal.pr_number,
                "title": f"Pull request {proposal.pr_number}",
                "body": "Review the changed repository files for defects.",
                "diff": evidence,
            },
            retrieved_chunks=[],
            sandbox_path=str(proposal.repository_path),
            llm_client=self._client,
            run_id=proposal.correlation_run_id,
        )

        findings: list[Finding] = []
        for item in draft.findings:
            try:
                findings.append(
                    Finding(
                        category=item.category,
                        severity=item.severity,
                        message=item.message,
                        file=item.file,
                        line=item.line,
                    )
                )
            except ValidationError:
                continue
            if len(findings) == 100:
                break

        return GeneratedFindings(
            findings=tuple(findings),
            summary=draft.summary_text[:4_000],
            tokens_in=draft.tokens_in,
            tokens_out=draft.tokens_out,
            cost_usd=draft.cost_usd,
        )


def _repository_evidence(proposal: Proposal) -> str:
    """Return bounded, line-numbered source text for the review prompt."""
    root = proposal.repository_path.resolve()
    blocks: list[str] = []
    used = 0

    for relative in proposal.files:
        if len(blocks) == MAX_EVIDENCE_FILES:
            break
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text:
            continue

        numbered = "\n".join(
            f"{number}: {line}" for number, line in enumerate(text.splitlines(), start=1)
        )
        block = f"File: {relative}\n{numbered}"
        if used + len(block) > MAX_EVIDENCE_BYTES:
            break
        blocks.append(block)
        used += len(block)

    return "\n\n".join(blocks)


__all__ = ["LLMFindingsGenerator"]
