from __future__ import annotations

import pytest

from enten.github.models import CheckRun, CheckRunOutput, Comment
from enten.queue import EffectState
from enten.queue.persistence import JsonValue
from enten.reporting.effects import (
    FencedGitHubEffects,
    GitHubEffects,
    IndeterminateGitHubEffectError,
)
from enten.reporting.payload import comment_marker
from enten.review.run import ReportPayload


def _payload(head_sha: str = "a" * 40) -> ReportPayload:
    return ReportPayload(
        owner="public",
        repo="repo",
        pr_number=42,
        head_sha=head_sha,
        conclusion="success",
        title="enten review: pass",
        summary="clean",
        comment="clean",
    )


class Client:
    calls: list[str]
    markers: list[str]

    def __init__(self) -> None:
        self.calls = []
        self.markers = []
        self.comments: dict[str, Comment] = {}

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        sha: str,
        conclusion: str,
        output: CheckRunOutput,
    ) -> CheckRun:
        _ = owner, repo, sha, conclusion, output
        self.calls.append("check")
        return CheckRun(id=11, status="completed", conclusion="success")

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        marker: str = "<!-- enten-bot -->",
    ) -> Comment:
        _ = owner, repo, number, body
        self.calls.append("comment")
        self.markers.append(marker)
        existing = self.comments.get(marker)
        comment_id = existing.id if existing is not None else len(self.comments) + 22
        comment = Comment(
            id=comment_id,
            html_url=f"https://example.test/comments/{comment_id}",
            body=f"{marker}\n{body}",
        )
        self.comments[marker] = comment
        return comment


async def test_fenced_effects_persist_attempt_before_each_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transitions: list[tuple[str, str]] = []
    prepared_requests: list[JsonValue] = []

    async def prepare(
        _run: str,
        key: str,
        _generation: int,
        *,
        marker: str,
        request: JsonValue,
    ):
        _ = marker
        transitions.append((key, "prepared"))
        prepared_requests.append(request)
        return {"state": EffectState.PREPARED, "external_id": None}

    async def attempt(_run: str, key: str, _generation: int) -> bool:
        transitions.append((key, "attempted"))
        return True

    async def complete(_run: str, key: str, _generation: int, *, external_id: str) -> bool:
        transitions.append((key, f"succeeded:{external_id}"))
        return True

    monkeypatch.setattr("enten.reporting.effects.prepare_effect", prepare)
    monkeypatch.setattr("enten.reporting.effects.record_effect_attempt", attempt)
    monkeypatch.setattr("enten.reporting.effects.complete_effect", complete)
    client = Client()

    receipt = await FencedGitHubEffects(client, run_id="run-1", generation=3).publish(_payload())

    assert client.calls == ["check", "comment"]
    assert transitions == [
        ("github-check", "prepared"),
        ("github-check", "attempted"),
        ("github-check", "succeeded:11"),
        ("github-comment", "prepared"),
        ("github-comment", "attempted"),
        ("github-comment", "succeeded:22"),
    ]
    assert receipt.check_run_id == "11"
    assert receipt.comment_id == "22"
    expected_marker = comment_marker("a" * 40)
    assert client.markers == [expected_marker]
    assert isinstance(prepared_requests[1], dict)
    assert prepared_requests[1]["marker"] == expected_marker


async def test_unfenced_effects_keep_out_of_order_heads_in_distinct_comments() -> None:
    client = Client()
    newer = _payload("b" * 40)
    older = _payload("a" * 40)

    newer_receipt = await GitHubEffects(client).publish(newer)
    older_receipt = await GitHubEffects(client).publish(older)
    newer_replay = await GitHubEffects(client).publish(newer)

    assert len(client.comments) == 2
    assert len(set(client.markers)) == 2
    assert newer_receipt.comment_id == newer_replay.comment_id
    assert older_receipt.comment_id != newer_receipt.comment_id


async def test_fenced_effects_never_retry_indeterminate_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def prepare(*_args: object, **_kwargs: object):
        return {"state": EffectState.INDETERMINATE, "external_id": None}

    monkeypatch.setattr("enten.reporting.effects.prepare_effect", prepare)
    client = Client()

    with pytest.raises(IndeterminateGitHubEffectError):
        _ = await FencedGitHubEffects(client, run_id="run-1", generation=3).publish(_payload())

    assert client.calls == []


async def test_fenced_effects_reconcile_completed_receipts_without_reposting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipts = {
        "github-check": {"state": EffectState.SUCCEEDED, "external_id": "11"},
        "github-comment": {"state": EffectState.SUCCEEDED, "external_id": "22"},
    }

    async def get_effect(_run: str, key: str):
        return receipts.get(key)

    monkeypatch.setattr("enten.reporting.effects.get_effect", get_effect)
    client = Client()

    receipt = await FencedGitHubEffects(client, run_id="run-1", generation=4).reconcile(_payload())

    assert receipt.state == "posted"
    assert receipt.check_run_id == "11"
    assert receipt.comment_id == "22"
    assert client.calls == []


async def test_fenced_effects_resume_comment_after_check_receipt_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_effect(_run: str, key: str):
        if key == "github-check":
            return {"state": EffectState.SUCCEEDED, "external_id": "11"}
        return None

    async def prepare(_run: str, key: str, _generation: int, **_kwargs: object):
        if key == "github-check":
            return {"state": EffectState.SUCCEEDED, "external_id": "11"}
        return {"state": EffectState.PREPARED, "external_id": None}

    async def attempt(_run: str, key: str, _generation: int) -> bool:
        assert key == "github-comment"
        return True

    async def complete(_run: str, key: str, _generation: int, *, external_id: str) -> bool:
        assert (key, external_id) == ("github-comment", "22")
        return True

    monkeypatch.setattr("enten.reporting.effects.get_effect", get_effect)
    monkeypatch.setattr("enten.reporting.effects.prepare_effect", prepare)
    monkeypatch.setattr("enten.reporting.effects.record_effect_attempt", attempt)
    monkeypatch.setattr("enten.reporting.effects.complete_effect", complete)
    client = Client()

    receipt = await FencedGitHubEffects(client, run_id="run-1", generation=4).reconcile(_payload())

    assert receipt.state == "posted"
    assert receipt.check_run_id == "11"
    assert receipt.comment_id == "22"
    assert client.calls == ["comment"]


async def test_fenced_effects_resume_missing_receipts_before_any_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_effect(_run: str, _key: str):
        return None

    async def prepare(_run: str, _key: str, _generation: int, **_kwargs: object):
        return {"state": EffectState.PREPARED, "external_id": None}

    async def attempt(_run: str, _key: str, _generation: int) -> bool:
        return True

    async def complete(_run: str, _key: str, _generation: int, *, external_id: str) -> bool:
        return external_id in {"11", "22"}

    monkeypatch.setattr("enten.reporting.effects.get_effect", get_effect)
    monkeypatch.setattr("enten.reporting.effects.prepare_effect", prepare)
    monkeypatch.setattr("enten.reporting.effects.record_effect_attempt", attempt)
    monkeypatch.setattr("enten.reporting.effects.complete_effect", complete)
    client = Client()

    receipt = await FencedGitHubEffects(client, run_id="run-1", generation=4).reconcile(_payload())

    assert receipt.state == "posted"
    assert client.calls == ["check", "comment"]


@pytest.mark.parametrize("ambiguous_state", [EffectState.ATTEMPTED, EffectState.INDETERMINATE])
async def test_fenced_effects_do_not_resume_ambiguous_receipt(
    monkeypatch: pytest.MonkeyPatch,
    ambiguous_state: EffectState,
) -> None:
    async def get_effect(_run: str, key: str):
        if key == "github-check":
            return {"state": ambiguous_state, "external_id": None}
        return None

    monkeypatch.setattr("enten.reporting.effects.get_effect", get_effect)
    client = Client()

    receipt = await FencedGitHubEffects(client, run_id="run-1", generation=4).reconcile(_payload())

    assert receipt.state == "indeterminate"
    assert client.calls == []
