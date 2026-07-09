"""Prompt-injection hardening: untrusted content wrappers and output validation."""

from __future__ import annotations

import re
import secrets
from typing import Literal

from pydantic import BaseModel

Kinds = Literal["diff", "pr_description", "code", "comment"]

_FILE_LINE_RE = re.compile(r"(\S+\.\w+):(\d+)")


class Finding(BaseModel):
    """A suppressed finding discovered during output validation."""

    file: str
    line: int
    reason: str


class ValidationResult(BaseModel):
    """Result of validating an LLM review output."""

    verified: bool
    output: str
    suppressed_findings: list[Finding]


def wrap_untrusted(text: str, kind: Kinds) -> str:
    """Wrap untrusted content in a randomized XML-like boundary.

    The boundary suffix is generated with ``secrets.token_hex(6)`` so an
    attacker cannot craft content that prematurely closes the tag.
    """
    boundary = secrets.token_hex(6)
    return f'<untrusted-{boundary} kind="{kind}">{text}</untrusted-{boundary}>'


def validate_review_output(text: str, available_files: list[str]) -> ValidationResult:
    """Validate that ``text`` contains at least one verifiable ``file:line`` reference.

    References pointing to files not present in ``available_files`` are treated
    as hallucinations and suppressed. When no verifiable references remain the
    output is replaced with ``<no verifiable findings>``.
    """
    available = set(available_files)
    suppressed: list[Finding] = []
    valid_found = False

    for match in _FILE_LINE_RE.finditer(text):
        file_path = match.group(1)
        line_no = int(match.group(2))
        if file_path in available:
            valid_found = True
        else:
            suppressed.append(
                Finding(
                    file=file_path,
                    line=line_no,
                    reason="hallucinated file reference",
                )
            )

    if not valid_found:
        return ValidationResult(
            verified=False,
            output="<no verifiable findings>",
            suppressed_findings=suppressed,
        )

    return ValidationResult(
        verified=True,
        output=text,
        suppressed_findings=suppressed,
    )
