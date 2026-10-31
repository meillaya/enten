"""Tests for versioned prompt loading and rendering."""

from __future__ import annotations

import pytest

from enten.llm.prompts import (  # pyright: ignore[reportMissingTypeStubs]
    MissingVariableError,
    list_versions,
    load_prompt,
    render,
)

_INITIAL_PROMPTS = [
    "system_base",
    "review",
    "test_gen",
    "patch",
    "failure_attribution",
    "hypothesis",
]


def _dummy_values(variables: list[str]) -> dict[str, str]:
    return {name: f"<{name}-value>" for name in variables}


def test_all_initial_prompts_load_and_render() -> None:
    """All shipped prompts load and render without missing-variable errors."""
    for slug in _INITIAL_PROMPTS:
        prompt = load_prompt(slug)
        _system, _user = render(prompt, _dummy_values(prompt.variables))
        assert isinstance(_system, str)
        assert isinstance(_user, str)


def test_render_raises_missing_variable_error() -> None:
    """Rendering a prompt without a declared variable raises MissingVariableError."""
    prompt = load_prompt("review")
    with pytest.raises(MissingVariableError):
        _ = render(prompt, {"diff": "foo"})


def test_load_latest_defaults_to_v1() -> None:
    """load_prompt('review') without a version returns version 1."""
    prompt = load_prompt("review")
    assert prompt.version == 1


def test_review_render_returns_nonempty_system_and_user() -> None:
    """Rendering review@v1 with all variables yields non-empty system and user."""
    prompt = load_prompt("review")
    system, user = render(
        prompt, {"diff": "x", "pr_title": "t", "pr_body": "b", "retrieved_context": "y"}
    )
    assert system
    assert user
    assert "x" in user
    assert "y" in user


def test_list_versions_returns_sorted_versions() -> None:
    """list_versions returns the available version numbers in ascending order."""
    versions = list_versions("review")
    assert versions == [1]
    assert all(isinstance(v, int) for v in versions)
