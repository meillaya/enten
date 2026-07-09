"""Async GitHub API client with App authentication and rate-limit handling."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from pathlib import Path
from time import time
from typing import Any

import httpx
import structlog

from enten.config import Settings
from enten.github.auth import create_jwt
from enten.github.models import CheckRun, CheckRunOutput, Comment, PRFile, PullRequest

logger = structlog.get_logger()

GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 30.0
INSTALLATION_TOKEN_TTL_SECONDS = 3000  # 50 minutes; GitHub tokens last 1 hour.
MAX_RATE_LIMIT_SLEEP_SECONDS = 60
MAX_RETRIES = 5


class GitHubClientError(Exception):
    """Base exception for GitHub client failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Async GitHub App REST API client.

    The client authenticates as a GitHub App using a signed JWT, exchanges
    that JWT for per-repository installation tokens, and provides a small
    set of typed helpers for the operations the agent needs.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.github_app_id = settings.github_app_id
        self.github_app_private_key_path = settings.github_app_private_key_path
        self.github_webhook_secret = settings.github_webhook_secret
        self.committer_email = settings.committer_email
        self.github_app_slug = settings.github_app_slug
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        # In-process cache: installation_id -> (token, expires_at_epoch).
        self._token_cache: dict[int, tuple[str, float]] = {}

    async def _get_jwt(self) -> str:
        """Return a fresh JWT for the configured GitHub App."""
        if self.settings.github_app_id is None:
            raise GitHubClientError("github_app_id is not configured")
        if self.settings.github_app_private_key_path is None:
            raise GitHubClientError("github_app_private_key_path is not configured")

        key_path = Path(self.settings.github_app_private_key_path)
        if not key_path.exists():
            raise GitHubClientError(f"private key not found: {key_path}")

        return create_jwt(str(self.settings.github_app_id), key_path)

    async def _find_installation_id(self, owner: str, repo: str) -> int:
        """Resolve the GitHub App installation ID for a repository."""
        jwt_token = await self._get_jwt()
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/installation",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        data = response.json()
        installation_id = data.get("id")
        if not isinstance(installation_id, int):
            raise GitHubClientError("installation response missing 'id'")
        return installation_id

    async def _get_installation_token(self, owner: str, repo: str) -> str:
        """Return a cached installation token, fetching a new one if needed."""
        installation_id = await self._find_installation_id(owner, repo)

        cached = self._token_cache.get(installation_id)
        if cached is not None:
            token, expires_at = cached
            if time() < expires_at - 60:  # 1 minute freshness buffer.
                return token

        jwt_token = await self._get_jwt()
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )
        data = response.json()
        token = data.get("token")
        if not isinstance(token, str):
            raise GitHubClientError("access token response missing 'token'")

        expires_at = time() + INSTALLATION_TOKEN_TTL_SECONDS
        self._token_cache[installation_id] = (token, expires_at)
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: dict[str, Any] | None = None,
        content: str | bytes | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with rate-limit handling and retries.

        * 403 with ``X-RateLimit-Remaining: 0``: sleep until
          ``X-RateLimit-Reset`` (capped at 60 seconds).
        * 429: exponential backoff with jitter, up to 5 attempts.
        * 401/404: never retried.
        """
        merged_headers = dict(headers) if headers else {}
        merged_headers.setdefault("Accept", "application/vnd.github+json")
        merged_headers.setdefault("X-GitHub-Api-Version", "2022-11-28")

        attempt = 0
        while True:
            response = await self._client.request(
                method,
                path,
                headers=merged_headers,
                json=json,
                content=content,
                params=params,
            )

            if response.status_code < 400:
                return response

            if response.status_code in (401, 404):
                response.raise_for_status()

            if response.status_code == 403:
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset_header = response.headers.get("X-RateLimit-Reset")
                if remaining == "0" and reset_header:
                    reset_ts = int(reset_header)
                    sleep_seconds = min(
                        max(reset_ts - int(time()), 0),
                        MAX_RATE_LIMIT_SLEEP_SECONDS,
                    )
                    logger.warning(
                        "github_primary_rate_limit_hit",
                        path=path,
                        sleep_seconds=sleep_seconds,
                    )
                    await asyncio.sleep(sleep_seconds)
                    continue

            if response.status_code == 429:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    response.raise_for_status()
                # Exponential backoff: 1s, 2s, 4s, 8s with full jitter.
                base_delay = 2 ** (attempt - 1)
                delay = random.uniform(0, base_delay)  # noqa: S311
                logger.warning(
                    "github_secondary_rate_limit_hit",
                    path=path,
                    attempt=attempt,
                    delay=delay,
                )
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()

    async def _api_headers(self, owner: str, repo: str) -> dict[str, str]:
        """Return headers authorized with an installation token."""
        token = await self._get_installation_token(owner, repo)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """Fetch a single pull request by owner/repo/number."""
        headers = await self._api_headers(owner, repo)
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers=headers,
        )
        return PullRequest.model_validate(response.json())

    async def get_pr_diff(self, owner: str, repo: str, number: int) -> str:
        """Fetch the raw diff text for a pull request."""
        headers = await self._api_headers(owner, repo)
        headers["Accept"] = "application/vnd.github.diff"
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers=headers,
        )
        return response.text

    async def list_pr_files(self, owner: str, repo: str, number: int) -> list[PRFile]:
        """List files changed by a pull request."""
        headers = await self._api_headers(owner, repo)
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{number}/files",
            headers=headers,
        )
        return [PRFile.model_validate(item) for item in response.json()]

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        marker: str = "<!-- enten-bot -->",
    ) -> Comment:
        """Post or update a PR comment containing ``marker``.

        Idempotent: if an existing comment on the PR already contains the
        marker, PATCH it in place; otherwise POST a new comment.
        """
        if marker not in body:
            body = f"{marker}\n{body}"

        headers = await self._api_headers(owner, repo)
        comments_response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            headers=headers,
        )
        comments = [Comment.model_validate(item) for item in comments_response.json()]

        for comment in comments:
            if marker in comment.body:
                response = await self._request(
                    "PATCH",
                    f"/repos/{owner}/{repo}/issues/comments/{comment.id}",
                    headers=headers,
                    json={"body": body},
                )
                return Comment.model_validate(response.json())

        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            headers=headers,
            json={"body": body},
        )
        return Comment.model_validate(response.json())

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        sha: str,
        conclusion: str,
        output: CheckRunOutput,
    ) -> CheckRun:
        """Create a check run for the given commit SHA."""
        headers = await self._api_headers(owner, repo)
        payload = {
            "name": "enten",
            "head_sha": sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": output.model_dump(exclude_none=True),
        }
        response = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/check-runs",
            headers=headers,
            json=payload,
        )
        return CheckRun.model_validate(response.json())

    async def update_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        conclusion: str,
        output: CheckRunOutput,
    ) -> CheckRun:
        """Update an existing check run."""
        headers = await self._api_headers(owner, repo)
        payload = {
            "status": "completed",
            "conclusion": conclusion,
            "output": output.model_dump(exclude_none=True),
        }
        response = await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/check-runs/{check_run_id}",
            headers=headers,
            json=payload,
        )
        return CheckRun.model_validate(response.json())

    def is_bot_author(self, pr: PullRequest) -> bool:
        """Return True when the PR appears to have been authored by this bot.

        Checks either the PR author's login or the head repository owner's
        login against the configured app slug.
        """
        slug = self.settings.github_app_slug
        if pr.user.login == slug:
            return True
        head_owner = pr.head.repo.owner.login if pr.head.repo else None
        return head_owner == slug

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
