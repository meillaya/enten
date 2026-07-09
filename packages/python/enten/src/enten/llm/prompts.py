"""Versioned prompt management.

Prompts live at ``{repo_root}/prompts/{slug}@v{N}.yaml`` and declare their
variables and output schema explicitly. Rendering uses simple Python
``str.format`` substitution — no Jinja — to keep the attack surface small.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, Field


class MissingVariableError(Exception):
    """Raised when a prompt is rendered without a declared variable."""


class Prompt(BaseModel):
    """A versioned prompt template with schema metadata."""

    slug: str
    version: int = Field(..., ge=1)
    system: str
    user_template: str
    variables: list[str]
    output_schema: dict[str, object]


_VERSION_RE = re.compile(r"@v(\d+)\.yaml$")


def _prompts_dir() -> Path:
    """Return the ``prompts/`` directory at the repository root.

    Falls back to ``prompts/`` under the current working directory when the
    source tree layout does not match the monorepo checkout.
    """
    source_root = Path(__file__).parents[6]
    prompts = source_root / "prompts"
    if prompts.is_dir():
        return prompts
    return Path.cwd() / "prompts"


def list_versions(slug: str) -> list[int]:
    """Return all available version numbers for ``slug``, sorted ascending."""
    versions: list[int] = []
    for path in _prompts_dir().glob(f"{slug}@v*.yaml"):
        match = _VERSION_RE.search(path.name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def load_prompt(slug: str, version: int | Literal["latest"] = "latest") -> Prompt:
    """Load a prompt from ``prompts/{slug}@v{version}.yaml``.

    When ``version`` is ``"latest"`` the highest available version is used.
    """
    prompts = _prompts_dir()

    if version == "latest":
        versions = list_versions(slug)
        if not versions:
            raise FileNotFoundError(f"No prompt versions found for slug {slug!r} in {prompts}")
        version = max(versions)

    path = prompts / f"{slug}@v{version}.yaml"
    with path.open("r", encoding="utf-8") as fh:
        data = cast(object, yaml.safe_load(fh))

    if not isinstance(data, dict):
        raise ValueError(f"Prompt file {path} did not contain a YAML mapping")

    return Prompt.model_validate(data)


def render(prompt: Prompt, variables: Mapping[str, object]) -> tuple[str, str]:
    """Return ``(system, user)`` for ``prompt`` with ``variables`` substituted.

    Raises:
        MissingVariableError: if any variable declared by the prompt is absent.
    """
    missing = [name for name in prompt.variables if name not in variables]
    if missing:
        raise MissingVariableError(f"Missing variables: {', '.join(missing)}")

    user = prompt.user_template.format(**variables)
    return prompt.system, user
