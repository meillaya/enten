"""Tests for ``enten.github.client`` and ``enten.github.auth``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from freezegun import freeze_time
from httpx import Response

from enten.config import Settings
from enten.github.auth import JWT_ALGORITHM, create_jwt
from enten.github.client import GitHubClient
from enten.github.models import CheckRunOutput, PullRequest

original_sleep = asyncio.sleep

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def private_key_file(tmp_path: Path) -> Path:
    """Generate a temporary RSA private key in PEM format."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "github-app.pem"
    key_path.write_bytes(pem)
    return key_path


@pytest.fixture
def settings(private_key_file: Path) -> Settings:
    """Settings configured for the mocked GitHub App."""
    return Settings(
        _env_file=None,
        github_app_id=123456,
        github_app_private_key_path=private_key_file,
        github_webhook_secret="webhook-secret",
        committer_email="bot@enten.dev",
        github_app_slug="enten-bot",
    )


@pytest.fixture
def client(settings: Settings) -> GitHubClient:
    """Unauthenticated client instance (mocked at transport layer)."""
    return GitHubClient(settings)


def _install_token_route(
    router: respx.MockRouter,
    installation_id: int = 42,
    token: str = "installation-token",
) -> None:
    """Mock the two App-auth endpoints used by the client."""
    router.get(
        path__regex=r"/repos/[^/]+/[^/]+/installation",
    ).mock(return_value=Response(200, json={"id": installation_id}))

    router.post(
        path=f"/app/installations/{installation_id}/access_tokens",
    ).mock(return_value=Response(201, json={"token": token}))


# ── auth ────────────────────────────────────────────────────────────────


def test_create_jwt_uses_rs256_and_ten_minute_ttl(private_key_file: Path) -> None:
    """``create_jwt`` returns a decodable RS256 token with the App ID."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(
        private_key_file.read_bytes(),
        password=None,
    )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with freeze_time("2026-07-09 12:00:00"):
        token = create_jwt("123456", private_key_file)
        decoded = pyjwt.decode(token, public_key, algorithms=[JWT_ALGORITHM])

    assert decoded["iss"] == "123456"
    assert decoded["iat"] == int(datetime(2026, 7, 9, 11, 59, tzinfo=UTC).timestamp())
    assert decoded["exp"] == int(datetime(2026, 7, 9, 12, 10, tzinfo=UTC).timestamp())


# ── get_pull_request ────────────────────────────────────────────────────


@respx.mock(assert_all_mocked=False)
async def test_get_pull_request_returns_parsed_pull_request(
    respx_mock: respx.MockRouter,
    client: GitHubClient,
) -> None:
    """A 200 from the pulls endpoint parses into a ``PullRequest``."""
    _install_token_route(respx_mock)

    payload = {
        "id": 1,
        "number": 42,
        "title": "Add feature",
        "state": "open",
        "user": {"login": "alice", "id": 1, "type": "User"},
        "head": {
            "ref": "feature",
            "sha": "abc123",
            "repo": {
                "id": 10,
                "full_name": "owner/repo",
                "owner": {"login": "alice", "id": 1, "type": "User"},
            },
        },
        "base": {
            "ref": "main",
            "sha": "def456",
            "repo": {
                "id": 11,
                "full_name": "owner/repo",
                "owner": {"login": "owner", "id": 2, "type": "Organization"},
            },
        },
        "body": "PR description",
        "html_url": "https://github.com/owner/repo/pull/42",
    }
    respx_mock.get(path="/repos/owner/repo/pulls/42").mock(return_value=Response(200, json=payload))

    pr = await client.get_pull_request("owner", "repo", 42)

    assert pr.number == 42
    assert pr.title == "Add feature"
    assert pr.user.login == "alice"


@respx.mock(assert_all_mocked=False)
async def test_get_pull_request_raises_on_404(
    respx_mock: respx.MockRouter,
    client: GitHubClient,
) -> None:
    """A 404 is propagated immediately without retries."""
    _install_token_route(respx_mock)
    respx_mock.get(path="/repos/owner/repo/pulls/42").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_pull_request("owner", "repo", 42)


@respx.mock(assert_all_mocked=False)
async def test_get_pull_request_retries_after_primary_rate_limit(
    respx_mock: respx.MockRouter,
    client: GitHubClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 with ``X-RateLimit-Remaining: 0`` sleeps until reset and retries."""
    _install_token_route(respx_mock)

    payload = {
        "id": 1,
        "number": 42,
        "title": "Add feature",
        "state": "open",
        "user": {"login": "alice", "id": 1, "type": "User"},
        "head": {
            "ref": "feature",
            "sha": "abc123",
            "repo": {
                "id": 10,
                "full_name": "owner/repo",
                "owner": {"login": "alice", "id": 1, "type": "User"},
            },
        },
        "base": {
            "ref": "main",
            "sha": "def456",
            "repo": {
                "id": 11,
                "full_name": "owner/repo",
                "owner": {"login": "owner", "id": 2, "type": "Organization"},
            },
        },
        "body": "PR description",
        "html_url": "https://github.com/owner/repo/pull/42",
    }

    reset_ts = int(datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC).timestamp())
    route = respx_mock.get(path="/repos/owner/repo/pulls/42")
    route.side_effect = [
        Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)},
        ),
        Response(200, json=payload),
    ]

    monkeypatch.setattr(asyncio, "sleep", lambda *_args, **_kwargs: original_sleep(0))
    with freeze_time("2026-07-09 12:00:00", tick=True):
        pr = await client.get_pull_request("owner", "repo", 42)

    assert pr.number == 42
    assert route.call_count == 2


# ── post_pr_comment ─────────────────────────────────────────────────────


@respx.mock(assert_all_mocked=False)
async def test_post_pr_comment_is_idempotent(
    respx_mock: respx.MockRouter,
    client: GitHubClient,
) -> None:
    """First call POSTs; a second call with the same marker PATCHes."""
    _install_token_route(respx_mock)

    marker = "<!-- enten-bot -->"
    list_route = respx_mock.get(path="/repos/owner/repo/issues/42/comments")
    list_route.mock(return_value=Response(200, json=[]))

    post_route = respx_mock.post(path="/repos/owner/repo/issues/42/comments")
    post_route.mock(
        return_value=Response(
            201,
            json={
                "id": 100,
                "html_url": "https://github.com/owner/repo/issues/42#issuecomment-100",
                "body": f"{marker}\nhello",
            },
        )
    )

    comment = await client.post_pr_comment("owner", "repo", 42, "hello")
    assert comment.id == 100
    assert marker in comment.body
    assert post_route.call_count == 1

    # Second call: existing comment returned by list, should PATCH.
    list_route.mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 100,
                    "html_url": "https://github.com/owner/repo/issues/42#issuecomment-100",
                    "body": f"{marker}\nhello",
                    "user": {"login": "enten-bot[bot]", "id": 99, "type": "Bot"},
                }
            ],
        )
    )

    patch_route = respx_mock.patch(path="/repos/owner/repo/issues/comments/100")
    patch_route.mock(
        return_value=Response(
            200,
            json={
                "id": 100,
                "html_url": "https://github.com/owner/repo/issues/42#issuecomment-100",
                "body": f"{marker}\nupdated",
            },
        )
    )

    updated = await client.post_pr_comment("owner", "repo", 42, "updated")
    assert updated.id == 100
    assert "updated" in updated.body
    assert post_route.call_count == 1
    assert patch_route.call_count == 1


# ── is_bot_author ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("enten-bot", True),
        ("alice", False),
    ],
)
def test_is_bot_author_matches_slug(settings: Settings, login: str, expected: bool) -> None:
    """Bot authorship is detected from the PR user login or head repo owner."""
    client = GitHubClient(settings)
    pr = PullRequest.model_validate(
        {
            "id": 1,
            "number": 1,
            "title": "x",
            "state": "open",
            "user": {"login": login, "id": 1, "type": "User"},
            "head": {
                "ref": "main",
                "sha": "a",
                "repo": {
                    "id": 1,
                    "full_name": "x/y",
                    "owner": {"login": "alice", "id": 2, "type": "User"},
                },
            },
            "base": {"ref": "main", "sha": "b"},
            "html_url": "https://github.com/x/y/pull/1",
        }
    )
    assert client.is_bot_author(pr) is expected


# ── check runs ──────────────────────────────────────────────────────────


@respx.mock(assert_all_mocked=False)
async def test_create_and_update_check_run_return_check_run(
    respx_mock: respx.MockRouter,
    client: GitHubClient,
) -> None:
    """Both check-run endpoints return a parsed ``CheckRun`` with an ``id``."""
    _install_token_route(respx_mock)

    output = CheckRunOutput(title="Enten check", summary="All good")

    create_route = respx_mock.post(path="/repos/owner/repo/check-runs")
    create_route.mock(
        return_value=Response(
            201,
            json={
                "id": 987,
                "html_url": "https://github.com/owner/repo/runs/987",
                "status": "completed",
                "conclusion": "success",
                "output": {"title": "Enten check", "summary": "All good"},
            },
        )
    )

    check_run = await client.create_check_run("owner", "repo", "abc123", "success", output)
    assert check_run.id == 987
    assert check_run.conclusion == "success"

    update_route = respx_mock.patch(path="/repos/owner/repo/check-runs/987")
    update_route.mock(
        return_value=Response(
            200,
            json={
                "id": 987,
                "html_url": "https://github.com/owner/repo/runs/987",
                "status": "completed",
                "conclusion": "failure",
                "output": {"title": "Enten check", "summary": "Found issues"},
            },
        )
    )

    updated = await client.update_check_run(
        "owner", "repo", 987, "failure", CheckRunOutput(title="Enten check", summary="Found issues")
    )
    assert updated.id == 987
    assert updated.conclusion == "failure"
