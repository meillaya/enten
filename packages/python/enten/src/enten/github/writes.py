"""GitHub comment and check-run write operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx
from pydantic import JsonValue

from enten.github.models import CheckRun, CheckRunOutput, Comment


class GitHubWriteTransport(Protocol):
    """Authenticated request surface required by GitHub write operations."""

    github_app_slug: str

    async def _api_headers(self, owner: str, repo: str) -> dict[str, str]: ...

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: dict[str, JsonValue] | None = None,
    ) -> httpx.Response: ...


def _is_expected_bot_comment(
    comment: Comment,
    *,
    github_app_slug: str,
) -> bool:
    author = comment.user
    if author is None or author.type != "Bot":
        return False
    slug = github_app_slug.removesuffix("[bot]")
    return author.login in {slug, f"{slug}[bot]"}


async def post_pr_comment(
    transport: GitHubWriteTransport,
    owner: str,
    repo: str,
    number: int,
    body: str,
    marker: str,
) -> Comment:
    marked_body = body if marker in body else f"{marker}\n{body}"
    headers = await transport._api_headers(owner, repo)
    comments_response = await transport._request(
        "GET",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        headers=headers,
    )
    comments = [Comment.model_validate(item) for item in comments_response.json()]

    for comment in comments:
        if marker in comment.body and _is_expected_bot_comment(
            comment,
            github_app_slug=transport.github_app_slug,
        ):
            response = await transport._request(
                "PATCH",
                f"/repos/{owner}/{repo}/issues/comments/{comment.id}",
                headers=headers,
                json={"body": marked_body},
            )
            return Comment.model_validate(response.json())

    response = await transport._request(
        "POST",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        headers=headers,
        json={"body": marked_body},
    )
    return Comment.model_validate(response.json())


async def create_check_run(
    transport: GitHubWriteTransport,
    owner: str,
    repo: str,
    sha: str,
    conclusion: str,
    output: CheckRunOutput,
) -> CheckRun:
    headers = await transport._api_headers(owner, repo)
    payload: dict[str, JsonValue] = {
        "name": "enten",
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": output.model_dump(exclude_none=True),
    }
    response = await transport._request(
        "POST",
        f"/repos/{owner}/{repo}/check-runs",
        headers=headers,
        json=payload,
    )
    return CheckRun.model_validate(response.json())


async def update_check_run(
    transport: GitHubWriteTransport,
    owner: str,
    repo: str,
    check_run_id: int,
    conclusion: str,
    output: CheckRunOutput,
) -> CheckRun:
    headers = await transport._api_headers(owner, repo)
    payload: dict[str, JsonValue] = {
        "status": "completed",
        "conclusion": conclusion,
        "output": output.model_dump(exclude_none=True),
    }
    response = await transport._request(
        "PATCH",
        f"/repos/{owner}/{repo}/check-runs/{check_run_id}",
        headers=headers,
        json=payload,
    )
    return CheckRun.model_validate(response.json())
