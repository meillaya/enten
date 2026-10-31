"""Pydantic v2 models for GitHub API responses.

These models intentionally capture only the subset of fields the agent
needs for self-detection, diff ingestion, and PR commenting. GitHub's
real payloads are much larger; ``extra="ignore"`` lets us stay lean
without breaking when GitHub adds new fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _GitHubModel(BaseModel):
    """Base model shared by all GitHub API shapes."""

    model_config = ConfigDict(extra="ignore")


class Repository(_GitHubModel):
    """Minimal repository representation."""

    id: int
    full_name: str
    owner: User


class User(_GitHubModel):
    """A GitHub user or app account."""

    login: str
    id: int
    type: str = "User"


class PullRequest(_GitHubModel):
    """A pull request as returned by the GitHub REST API."""

    id: int
    number: int
    title: str
    state: str
    user: User
    head: PRBranch
    base: PRBranch
    body: str | None = None
    html_url: str
    diff_url: str | None = None


class PRBranch(_GitHubModel):
    """Branch reference embedded in a pull request."""

    ref: str
    sha: str
    repo: Repository | None = None


class PRFile(_GitHubModel):
    """A single file entry from the PR files endpoint."""

    sha: str
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    previous_filename: str | None = None


class Comment(_GitHubModel):
    """A PR issue comment."""

    id: int
    html_url: str
    body: str
    user: User | None = None


class Annotation(_GitHubModel):
    """One file/line annotation attached to a check run."""

    path: str
    start_line: int
    end_line: int
    annotation_level: str
    message: str
    title: str | None = None


class CheckRunOutput(_GitHubModel):
    """Check-run output payload."""

    title: str
    summary: str
    text: str | None = None
    annotations: list[Annotation] | None = None


class CheckRun(_GitHubModel):
    """A check run as returned by the GitHub REST API."""

    id: int
    html_url: str | None = None
    status: str
    conclusion: str | None = None
    output: CheckRunOutput | None = None


# Resolve forward references now that all classes are defined.
Repository.model_rebuild()
PullRequest.model_rebuild()
