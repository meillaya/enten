"""Smoke tests for documentation deliverables."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DECISIONS = ROOT / "docs" / "decisions"
LICENSE = ROOT / "LICENSE"


def _headings(text: str) -> set[str]:
    return {
        line.strip().lstrip("#").strip().lower()
        for line in text.splitlines()
        if line.strip().startswith("#")
    }


def test_readme_has_required_headings() -> None:
    text = README.read_text(encoding="utf-8")
    headings = _headings(text)
    required = {
        "quickstart",
        "hosted review",
        "deployment",
        "checks",
        "documentation",
        "license",
    }
    missing = required - headings
    assert not missing, f"Missing README headings: {missing}"


def test_readme_relative_links_resolve() -> None:
    """Every relative README link points at a path in this checkout."""
    text = README.read_text(encoding="utf-8")
    broken: list[str] = []
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path = ROOT / target.split("#", 1)[0]
        if not path.exists():
            broken.append(target)
    assert not broken, f"Broken README links: {broken}"


def test_at_least_ten_adrs() -> None:
    assert DECISIONS.is_dir(), f"{DECISIONS} is not a directory"
    adrs = [p for p in DECISIONS.iterdir() if p.is_file() and p.suffix == ".md"]
    assert len(adrs) >= 10, f"Expected >=10 ADRs, found {len(adrs)}"


def test_license_exists() -> None:
    assert LICENSE.is_file(), f"{LICENSE} does not exist"
    text = LICENSE.read_text(encoding="utf-8")
    assert "MIT" in text, "LICENSE does not mention MIT"
