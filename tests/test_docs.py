"""Smoke tests for documentation deliverables."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

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


def test_docs_relative_links_resolve() -> None:
    """Every relative link under docs points at a path in this checkout."""
    broken: list[str] = []
    for path in (ROOT / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            linked = (path.parent / target.split("#", 1)[0]).resolve()
            if not linked.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, f"Broken docs links: {broken}"
