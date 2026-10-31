"""Detect a sensible local verification command without requiring flags."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Final

_EXCLUDED = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
MAX_SCAN_FILES: Final = 5_000
_PYTEST_CONFIG_FILES = ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini")


class VerificationDetectionError(ValueError):
    """No safe verification command could be inferred."""


def detect_verify_command(root: Path) -> tuple[str, ...]:
    """Return a verification command inferred from the checkout layout.

    Python tests win over JavaScript and Makefile projects so that a polyglot
    monorepo does not silently run the wrong suite.
    """
    root = root.resolve()
    if not root.is_dir():
        raise VerificationDetectionError(f"not a directory: {root}")

    if _has_python_tests(root):
        return ("python", "-m", "pytest", "-q")
    package_command = _package_test(root)
    if package_command is not None:
        return package_command
    if _makefile_has_test(root):
        return ("make", "test")
    if (root / "go.mod").is_file():
        return ("go", "test", "./...")
    if (root / "Cargo.toml").is_file():
        return ("cargo", "test")
    if any(_iter_files(root, {".py"})):
        return ("python", "-m", "compileall", "-q", ".")
    raise VerificationDetectionError(
        "could not detect a verification command; pass --verify with an argv string"
    )


def _has_python_tests(root: Path) -> bool:
    for name in _PYTEST_CONFIG_FILES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "pytest" in text or "[tool:pytest]" in text:
            return True
    for path in _iter_files(root, {".py"}):
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            return True
        if "tests" in path.parts and path.suffix == ".py":
            return True
    return False


def _package_test(root: Path) -> tuple[str, ...] | None:
    package = root / "package.json"
    if not package.is_file():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    test = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(test, str) or not test.strip():
        return None
    if (root / "pnpm-lock.yaml").is_file():
        return ("pnpm", "test")
    return ("npm", "test")


def _makefile_has_test(root: Path) -> bool:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return False
    try:
        lines = makefile.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.startswith("test:") for line in lines)


def _iter_files(root: Path, suffixes: set[str]) -> Iterator[Path]:
    scanned = 0
    for path in root.rglob("*"):
        if scanned >= MAX_SCAN_FILES:
            return
        if any(part in _EXCLUDED for part in path.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        scanned += 1
        if path.suffix in suffixes:
            yield path
