from __future__ import annotations

from typing import Never, Protocol, final

from enten.github.models import CheckRun, CheckRunOutput, Comment
from enten.queue import (
    EffectState,
    complete_effect,
    mark_effect_indeterminate,
    prepare_effect,
    record_effect_attempt,
)
from enten.queue.persistence import JsonValue
from enten.review.run import EffectReceipt, ReportPayload


class GitHubEffectClient(Protocol):
    """Narrow GitHub surface that cannot write repository contents or branches."""

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        sha: str,
        conclusion: str,
        output: CheckRunOutput,
    ) -> CheckRun: ...

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        marker: str = "<!-- enten-bot -->",
    ) -> Comment: ...


@final
class GitHubEffects:
    def __init__(self, client: GitHubEffectClient) -> None:
        self._client = client

    async def publish(self, payload: ReportPayload) -> EffectReceipt:
        if payload.owner is None or payload.repo is None or payload.pr_number == 0:
            raise ValueError("GitHub reporting requires owner, repo, and a positive PR number")
        check = await self._client.create_check_run(
            payload.owner,
            payload.repo,
            payload.head_sha,
            payload.conclusion,
            CheckRunOutput(title=payload.title, summary=payload.summary),
        )
        comment = await self._client.post_pr_comment(
            payload.owner,
            payload.repo,
            payload.pr_number,
            payload.comment,
        )
        return EffectReceipt(
            state="posted",
            check_run_id=str(check.id),
            comment_id=str(comment.id),
            check_run_url=check.html_url,
            comment_url=comment.html_url,
        )


@final
class IndeterminateGitHubEffectError(RuntimeError):
    """Raised when retrying a GitHub write could duplicate an effect."""


@final
class FencedGitHubEffects:
    """Publish GitHub writes through the durable queue effect ledger."""

    def __init__(self, client: GitHubEffectClient, *, run_id: str, generation: int) -> None:
        self._client = client
        self._run_id = run_id
        self._generation = generation

    async def publish(self, payload: ReportPayload) -> EffectReceipt:
        if payload.owner is None or payload.repo is None or payload.pr_number == 0:
            raise ValueError("GitHub reporting requires owner, repo, and a positive PR number")

        check_id, check_url = await self._publish_check(payload)
        comment_id, comment_url = await self._publish_comment(payload)
        return EffectReceipt(
            state="posted",
            check_run_id=check_id,
            comment_id=comment_id,
            check_run_url=check_url,
            comment_url=comment_url,
        )

    async def _publish_check(self, payload: ReportPayload) -> tuple[str, str | None]:
        assert payload.owner is not None and payload.repo is not None
        key = "github-check"
        request: JsonValue = {
            "owner": payload.owner,
            "repo": payload.repo,
            "sha": payload.head_sha,
            "conclusion": payload.conclusion,
            "title": payload.title,
            "summary": payload.summary,
        }
        replay_id = await self._begin(key, request)
        if replay_id is not None:
            return replay_id, None
        try:
            check = await self._client.create_check_run(
                payload.owner,
                payload.repo,
                payload.head_sha,
                payload.conclusion,
                CheckRunOutput(title=payload.title, summary=payload.summary),
            )
        except Exception as exc:
            await self._indeterminate(key, exc)
        check_id = str(check.id)
        await self._complete(key, check_id)
        return check_id, check.html_url

    async def _publish_comment(self, payload: ReportPayload) -> tuple[str, str | None]:
        assert payload.owner is not None and payload.repo is not None
        key = "github-comment"
        request: JsonValue = {
            "owner": payload.owner,
            "repo": payload.repo,
            "pr_number": payload.pr_number,
            "body": payload.comment,
        }
        replay_id = await self._begin(key, request)
        if replay_id is not None:
            return replay_id, None
        try:
            comment = await self._client.post_pr_comment(
                payload.owner,
                payload.repo,
                payload.pr_number,
                payload.comment,
            )
        except Exception as exc:
            await self._indeterminate(key, exc)
        comment_id = str(comment.id)
        await self._complete(key, comment_id)
        return comment_id, comment.html_url

    async def _begin(self, key: str, request: JsonValue) -> str | None:
        receipt = await prepare_effect(
            self._run_id,
            key,
            self._generation,
            marker=f"enten:{self._run_id}:{key}",
            request=request,
        )
        if receipt["state"] is EffectState.SUCCEEDED:
            external_id = receipt["external_id"]
            if external_id is None:
                raise IndeterminateGitHubEffectError("succeeded effect has no external id")
            return external_id
        if receipt["state"] is not EffectState.PREPARED:
            raise IndeterminateGitHubEffectError(
                f"GitHub effect {key} is {receipt['state']} and cannot be retried"
            )
        if not await record_effect_attempt(self._run_id, key, self._generation):
            raise IndeterminateGitHubEffectError(
                f"lost queue lease before attempting GitHub effect {key}"
            )
        return None

    async def _complete(self, key: str, external_id: str) -> None:
        if not await complete_effect(
            self._run_id,
            key,
            self._generation,
            external_id=external_id,
        ):
            raise IndeterminateGitHubEffectError(
                f"GitHub effect {key} succeeded but its receipt could not be recorded"
            )

    async def _indeterminate(self, key: str, exc: Exception) -> Never:
        _ = await mark_effect_indeterminate(
            self._run_id,
            key,
            self._generation,
            f"{type(exc).__name__}: {exc}",
        )
        raise IndeterminateGitHubEffectError(
            f"GitHub effect {key} outcome is indeterminate"
        ) from exc


@final
class NoopEffects:
    async def publish(self, payload: ReportPayload) -> EffectReceipt:
        _ = payload
        return EffectReceipt(state="skipped", detail="local review has no remote effects")


__all__ = [
    "FencedGitHubEffects",
    "GitHubEffectClient",
    "GitHubEffects",
    "IndeterminateGitHubEffectError",
    "NoopEffects",
]
