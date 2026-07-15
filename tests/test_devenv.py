from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_devenv_declares_pinned_language_and_service_contract() -> None:
    source = (ROOT / "devenv.nix").read_text(encoding="utf-8")
    assert "pkgs.python312" in source
    assert "pkgs.nodejs_22" in source
    assert "pkgs.postgresql_16" in source
    assert "extensions.pgvector" in source
    for package in ("uv", "semgrep", "gitleaks", "bandit", "tree-sitter", "sqlite"):
        assert package in source


def test_devenv_inputs_are_locked() -> None:
    lock = json.loads((ROOT / "devenv.lock").read_text(encoding="utf-8"))
    assert lock["nodes"]["nixpkgs"]["locked"]["rev"]


def test_devenv_evaluates() -> None:
    if shutil.which("devenv") is None:
        pytest.skip("devenv executable not installed")
    subprocess.run(
        ["devenv", "test"],
        cwd=ROOT,
        check=True,
        timeout=300,
    )
