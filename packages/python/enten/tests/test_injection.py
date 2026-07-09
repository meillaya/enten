"""Tests for prompt-injection hardening wrappers and output validation."""

from __future__ import annotations

import re

from enten.llm.injection import (  # pyright: ignore[reportMissingTypeStubs]
    ValidationResult,
    validate_review_output,
    wrap_untrusted,
)


def test_wrap_untrusted_produces_tagged_boundary() -> None:
    """wrap_untrusted encloses text in a randomized <untrusted-*> boundary."""
    wrapped = wrap_untrusted("hello", "diff")
    pattern = re.compile(r"<untrusted-[0-9a-f]{12} kind=\"diff\">hello</untrusted-[0-9a-f]{12}>")
    assert pattern.fullmatch(wrapped), wrapped


def test_wrap_untrusted_uses_different_boundaries_per_call() -> None:
    """Each call generates a fresh boundary suffix to prevent token escape."""
    first = wrap_untrusted("a", "code")
    second = wrap_untrusted("a", "code")
    assert first != second
    # Boundaries are 12-character hex strings.
    assert re.search(r"<untrusted-([0-9a-f]{12}) kind=\"code\">", first)
    assert re.search(r"<untrusted-([0-9a-f]{12}) kind=\"code\">", second)


def test_validate_review_output_rejects_missing_references() -> None:
    """Output without any file:line reference is rejected."""
    result = validate_review_output("This code looks buggy", [])
    assert result == ValidationResult(
        verified=False,
        output="<no verifiable findings>",
        suppressed_findings=[],
    )


def test_validate_review_output_accepts_verifiable_reference() -> None:
    """Output with a file:line reference in the available file set is accepted."""
    result = validate_review_output("Bug in src/x.py:42", ["src/x.py"])
    assert result.verified is True
    assert result.output == "Bug in src/x.py:42"
    assert result.suppressed_findings == []


def test_validate_review_output_rejects_hallucinated_reference() -> None:
    """Output referencing a file not in the available set is treated as hallucinated."""
    result = validate_review_output("Bug in src/hallucinated.py:99", ["src/x.py"])
    assert result.verified is False
    assert result.output == "<no verifiable findings>"
    assert len(result.suppressed_findings) == 1
    finding = result.suppressed_findings[0]
    assert finding.file == "src/hallucinated.py"
    assert finding.line == 99
    assert "hallucinated" in finding.reason
