"""GitHub PR comment markdown formatter for enten reviews."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

BOT_MARKER = "<!-- enten-bot -->"
MAX_INLINE_DIFF_CHARS = 20_000


class Finding(BaseModel):
    """A single static-analysis or security finding."""

    category: str
    severity: str
    message: str
    file: str = ""
    line: int = 0


class PRReview(BaseModel):
    """Aggregated review payload to be rendered as a PR comment."""

    summary: dict[str, Any] = Field(default_factory=dict)
    static_findings: list[Finding] = Field(default_factory=list)
    security_findings: list[Finding] = Field(default_factory=list)
    suggested_tests: list[str] = Field(default_factory=list)
    suggested_patch: str = ""
    verdict: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


def sanitize_markdown_inline(text: str) -> str:
    """Return untrusted text that cannot change Markdown line or mention structure."""
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<iframe\b[^>]*>.*?</iframe>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<object\b[^>]*>.*?</object>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<embed\b[^>]*>", "", text, flags=re.IGNORECASE)
    safe = "".join(_safe_markdown_character(character) for character in text)
    return html.escape(safe)


def sanitize_markdown_block(text: str) -> str:
    """Return untrusted multiline code without fence or mention escapes."""
    safe = "".join(
        character if character == "\n" else _safe_markdown_character(character)
        for character in text
    )
    return html.escape(safe)


def _safe_markdown_character(character: str) -> str:
    if character == "`":
        return "ˋ"
    if character == "@":
        return "＠"
    if unicodedata.category(character).startswith("C"):
        return "�"
    return character


def _finding_location(file: str, line: int) -> str:
    """Return a file:line reference, falling back to the file alone."""
    if file and line > 0:
        return f"{file}:{line}"
    if file:
        return file
    return "unknown location"


def _grouped_findings_rows(findings: list[Finding]) -> str:
    """Render findings grouped by severity."""
    if not findings:
        return "_No findings._"

    by_severity: dict[str, list[Finding]] = {}
    for finding in findings:
        by_severity.setdefault(finding.severity or "unknown", []).append(finding)

    rows: list[str] = []
    for severity in sorted(by_severity.keys()):
        rows.append(f"**{severity.upper()}**")
        for finding in by_severity[severity]:
            loc = sanitize_markdown_inline(_finding_location(finding.file, finding.line))
            msg = sanitize_markdown_inline(finding.message)
            rows.append(f"- `{loc}` — {msg}")
        rows.append("")

    return "\n".join(rows).rstrip()


def _badge(verdict: str) -> str:
    """Return a markdown badge reflecting the verdict."""
    verdict_lower = verdict.lower().strip()
    if verdict_lower in {"pass", "passed", "success", "ok"}:
        return "![pass](https://img.shields.io/badge/enten-pass-brightgreen)"
    if verdict_lower in {"fail", "failed", "failure", "rejected"}:
        return "![fail](https://img.shields.io/badge/enten-fail-red)"
    return "![review](https://img.shields.io/badge/enten-review-blue)"


def render_pr_comment(review: PRReview) -> str:
    """Render a PRReview as a GitHub-flavored markdown comment.

    The returned string always contains the ``<!-- enten-bot -->`` marker
    so that T12's idempotent posting logic can identify it.
    """
    summary = review.summary or {}
    bot_name = summary.get("bot_name", "enten-bot")
    author = summary.get("author", "unknown")
    sha = summary.get("sha", "unknown")
    verdict = review.verdict or summary.get("verdict", "review")

    lines: list[str] = [BOT_MARKER, ""]

    # Summary card
    lines.extend(
        [
            f"{_badge(verdict)} | **{sanitize_markdown_inline(str(bot_name))}**",
            "",
            f"- **Author:** {sanitize_markdown_inline(str(author))}",
            f"- **Commit:** `{sanitize_markdown_inline(str(sha))}`",
            f"- **Verdict:** {sanitize_markdown_inline(str(verdict))}",
            "",
        ]
    )

    # Static analysis
    lines.extend(
        [
            "<details>",
            "<summary><b>Static analysis</b> (Semgrep findings)</summary>",
            "",
            _grouped_findings_rows(review.static_findings),
            "",
            "</details>",
            "",
        ]
    )

    # Security findings
    lines.extend(
        [
            "<details>",
            "<summary><b>Security findings</b></summary>",
            "",
            _grouped_findings_rows(review.security_findings),
            "",
            "</details>",
            "",
        ]
    )

    # Suggested tests
    if review.suggested_tests:
        tests_md = "\n".join(f"- {sanitize_markdown_inline(t)}" for t in review.suggested_tests)
        lines.extend(
            [
                "<details>",
                "<summary><b>Suggested tests</b></summary>",
                "",
                tests_md,
                "",
                "</details>",
                "",
            ]
        )

    # Suggested patch
    if review.suggested_patch:
        patch = review.suggested_patch
        if len(patch) > MAX_INLINE_DIFF_CHARS:
            patch = patch[:MAX_INLINE_DIFF_CHARS] + "\n\n... (patch truncated due to size)"
        lines.extend(
            [
                "<details>",
                "<summary><b>Suggested patch</b></summary>",
                "",
                "```diff",
                sanitize_markdown_block(patch),
                "```",
                "",
                "</details>",
                "",
            ]
        )

    # Deterministic verdict
    evidence_md = "\n".join(
        f"- **{sanitize_markdown_inline(str(key))}:** {sanitize_markdown_inline(str(value))}"
        for key, value in (review.evidence or {}).items()
    )
    lines.extend(
        [
            "<details>",
            "<summary><b>Deterministic verdict</b></summary>",
            "",
            f"**Verdict:** {sanitize_markdown_inline(str(verdict))}",
            "",
            "**Evidence:**" if evidence_md else "",
            evidence_md,
            "",
            "</details>",
            "",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"
