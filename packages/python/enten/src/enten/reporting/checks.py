"""GitHub Checks API reporter helpers."""

from __future__ import annotations

from typing import Any

from enten.github.client import GitHubClient
from enten.github.models import CheckRun, CheckRunOutput

MAX_TITLE_LENGTH = 80
MAX_SUMMARY_LENGTH = 65_535


def _validate_output(output: dict[str, Any]) -> None:
    """Validate GitHub Check Run output field length limits."""
    title = output.get("title", "")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"output.title exceeds {MAX_TITLE_LENGTH} characters: {len(title)}")

    summary = output.get("summary", "")
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"output.summary exceeds {MAX_SUMMARY_LENGTH} characters: {len(summary)}")


def _to_check_run_output(output: dict[str, Any]) -> CheckRunOutput:
    """Convert a plain dict to a CheckRunOutput model, validating limits."""
    _validate_output(output)
    return CheckRunOutput(
        title=output["title"],
        summary=output["summary"],
        text=output.get("text"),
    )


async def post_check_run(
    client: GitHubClient,
    owner: str,
    repo: str,
    sha: str,
    conclusion: str,
    output: dict[str, Any],
) -> CheckRun:
    """Create a new completed check run on the given commit SHA."""
    check_output = _to_check_run_output(output)
    return await client.create_check_run(owner, repo, sha, conclusion, check_output)


async def update_check_run(
    client: GitHubClient,
    owner: str,
    repo: str,
    check_run_id: str,
    conclusion: str,
    output: dict[str, Any],
) -> CheckRun:
    """Update an existing check run with a new conclusion and output."""
    check_output = _to_check_run_output(output)
    return await client.update_check_run(owner, repo, int(check_run_id), conclusion, check_output)
