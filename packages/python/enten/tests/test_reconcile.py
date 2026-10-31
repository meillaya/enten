"""Tests for operator reconciliation of ambiguous GitHub effects."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from enten.config import Settings
from enten.reconcile import ReconcileError, reconcile

HEAD = "a84d88e7554fc1fa21bcbc4efae3c0a85e3a0000"


class FakeConnection:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.executed: list[str] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        _ = args
        if "FROM jobs" in query:
            return {"payload": json.dumps(self.payload), "state": "failed"}
        if "FROM external_effects" in query:
            return {"state": "indeterminate", "external_id": None}
        return None

    async def execute(self, query: str, *args: object) -> str:
        _ = args
        self.executed.append(query)
        return "UPDATE 1"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def close(self) -> None:
        return None


class FakeGitHub:
    def __init__(self, settings: Settings) -> None:
        _ = settings
        self.closed = False

    async def get_check_run(self, owner: str, repo: str, check_id: int) -> dict[str, Any]:
        _ = owner, repo, check_id
        return {"head_sha": HEAD, "name": "enten", "app": {"id": 5557296}}

    async def get_issue_comment(self, owner: str, repo: str, comment_id: int) -> dict[str, Any]:
        _ = owner, repo, comment_id
        from enten.reporting.payload import comment_marker

        return {"body": f"review\n{comment_marker(HEAD)}"}

    async def close(self) -> None:
        self.closed = True


def _payload() -> dict[str, Any]:
    return {
        "version": "1",
        "installation_id": 9,
        "delivery_id": "11111111-1111-4111-8111-111111111111",
        "repository": {"id": 101, "full_name": "public/repo"},
        "pull_request": {"number": 42, "head_sha": HEAD},
        "action": "opened",
        "actor": {"id": 4, "login": "alice"},
        "review_contract": "b" * 64,
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://enten:enten@postgres:5432/enten",
            "github_app_id": 5557296,
            "github_app_slug": "enten-live-test",
        }
    )


async def test_reconcile_adopts_check_and_requeues(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(_payload())

    async def fake_connect(dsn: str) -> FakeConnection:
        _ = dsn
        return connection

    monkeypatch.setattr("enten.reconcile.Settings", _settings)
    monkeypatch.setattr("enten.reconcile.asyncpg.connect", fake_connect)
    monkeypatch.setattr("enten.reconcile.GitHubClient", FakeGitHub)

    result = await reconcile("run-1", check_id=124620650964)

    assert result["job_state"] == "queued"
    assert result["check_id"] == 124620650964
    assert any("UPDATE external_effects" in query for query in connection.executed)
    assert any("UPDATE jobs" in query for query in connection.executed)


async def test_reconcile_rejects_a_check_for_another_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(_payload())

    class WrongHeadGitHub(FakeGitHub):
        async def get_check_run(
            self, owner: str, repo: str, check_id: int
        ) -> dict[str, Any]:
            _ = owner, repo, check_id
            return {"head_sha": "0" * 40, "name": "enten", "app": {"id": 5557296}}

    async def fake_connect(dsn: str) -> FakeConnection:
        _ = dsn
        return connection

    monkeypatch.setattr("enten.reconcile.Settings", _settings)
    monkeypatch.setattr("enten.reconcile.asyncpg.connect", fake_connect)
    monkeypatch.setattr("enten.reconcile.GitHubClient", WrongHeadGitHub)

    with pytest.raises(ReconcileError, match="does not belong"):
        await reconcile("run-1", check_id=124620650964)

    assert connection.executed == []
