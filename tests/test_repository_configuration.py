from __future__ import annotations

from pathlib import Path

import yaml
from enten.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_python_env_stubs_only_name_supported_settings(test_env: dict[str, str]) -> None:
    supported_names = {name.upper() for name in Settings.model_fields}

    assert test_env.keys() <= supported_names


def test_dependabot_watches_workspace_manifest_directories() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    configured_directories = {
        update["package-ecosystem"]: update["directory"] for update in config["updates"]
    }

    assert configured_directories == {
        "pip": "/packages/python/enten",
        "npm": "/",
    }


def test_typescript_ci_migrates_its_isolated_database() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = workflow["jobs"]["typescript"]["steps"]

    assert any(
        step.get("uses", "").startswith("astral-sh/setup-uv@") for step in steps
    )
    assert any(step.get("run") == "bash scripts/db-migrate.sh" for step in steps)
