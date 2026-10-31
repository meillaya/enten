"""Tests for local verification command detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from enten.review.detect import VerificationDetectionError, detect_verify_command


def test_detects_python_tests(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("python", "-m", "pytest", "-q")


def test_prefers_python_over_package_json(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("python", "-m", "pytest", "-q")


def test_detects_package_manager_test(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("pnpm", "test")


def test_detects_make_go_and_cargo(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("make", "test")
    (tmp_path / "Makefile").unlink()
    (tmp_path / "go.mod").write_text("module example.test/app\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("go", "test", "./...")
    (tmp_path / "go.mod").unlink()
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"app\"\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("cargo", "test")


def test_python_without_tests_falls_back_to_compileall(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    assert detect_verify_command(tmp_path) == ("python", "-m", "compileall", "-q", ".")


def test_no_known_project_raises(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("empty\n", encoding="utf-8")
    with pytest.raises(VerificationDetectionError):
        detect_verify_command(tmp_path)
