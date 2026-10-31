"""Operator reconciliation for an ambiguous GitHub external effect."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from enten.config import Settings
from enten.github.client import GitHubClient
from enten.migration_operations import asyncpg_url
from enten.reporting.payload import comment_marker
from enten.review.envelope import ReviewJobEnvelope


class ReconcileError(RuntimeError):
    """The observed GitHub object cannot be adopted for this run."""


async def reconcile(
    run_id: str,
    *,
    check_id: int | None = None,
    comment_id: int | None = None,
) -> dict[str, Any]:
    """Adopt observed GitHub objects and requeue the job for the missing work."""
    if check_id is None and comment_id is None:
        raise ReconcileError("provide at least one of check_id or comment_id")

    settings = Settings()
    connection = await asyncpg.connect(asyncpg_url(settings.database_url))
    client = GitHubClient(settings)
    try:
        job = await connection.fetchrow(
            "SELECT payload, state FROM jobs WHERE run_id = $1",
            run_id,
        )
        if job is None:
            raise ReconcileError(f"job not found: {run_id}")

        raw_payload = job["payload"]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        envelope = ReviewJobEnvelope.model_validate(payload)
        owner, repo = envelope.repository.full_name.split("/", maxsplit=1)
        head_sha = envelope.pull_request.head_sha

        if check_id is not None:
            check = await client.get_check_run(owner, repo, check_id)
            app = check.get("app") or {}
            if check.get("head_sha") != head_sha:
                raise ReconcileError("check run does not belong to the reviewed head")
            if check.get("name") != "enten":
                raise ReconcileError("check run is not owned by enten")
            if app.get("id") != settings.github_app_id:
                raise ReconcileError("check run belongs to a different GitHub App")

        if comment_id is not None:
            comment = await client.get_issue_comment(owner, repo, comment_id)
            body = str(comment.get("body") or "")
            if comment_marker(head_sha) not in body:
                raise ReconcileError("comment does not carry this head's enten marker")

        async with connection.transaction():
            if check_id is not None:
                await _adopt_effect(
                    connection,
                    run_id,
                    "github-check",
                    str(check_id),
                )
            if comment_id is not None:
                await _adopt_effect(
                    connection,
                    run_id,
                    "github-comment",
                    str(comment_id),
                )
            updated = await connection.execute(
                """
                UPDATE jobs
                SET state = 'queued',
                    attempts = 0,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    last_error = 'operator reconciliation'
                WHERE run_id = $1
                  AND state <> 'done'
                """,
                run_id,
            )
            if updated != "UPDATE 1":
                raise ReconcileError(f"job cannot be requeued from state {job['state']!r}")

        return {
            "run_id": run_id,
            "check_id": check_id,
            "comment_id": comment_id,
            "job_state": "queued",
        }
    finally:
        await client.close()
        await connection.close()


async def _adopt_effect(
    connection: asyncpg.Connection,
    run_id: str,
    effect_key: str,
    external_id: str,
) -> None:
    row = await connection.fetchrow(
        """
        SELECT state, external_id
        FROM external_effects
        WHERE run_id = $1 AND effect_key = $2
        """,
        run_id,
        effect_key,
    )
    if row is None:
        raise ReconcileError(f"effect not found: {run_id} {effect_key}")
    if row["state"] == "succeeded":
        if str(row["external_id"]) != external_id:
            raise ReconcileError(f"effect already succeeded with a different ID: {effect_key}")
        return
    if row["state"] != "indeterminate":
        raise ReconcileError(
            f"effect {effect_key} is {row['state']!r}, not 'indeterminate'"
        )
    updated = await connection.execute(
        """
        UPDATE external_effects
        SET state = 'succeeded',
            external_id = $3,
            completed_at = now(),
            last_error = NULL
        WHERE run_id = $1
          AND effect_key = $2
          AND state = 'indeterminate'
        """,
        run_id,
        effect_key,
        external_id,
    )
    if updated != "UPDATE 1":
        raise ReconcileError(f"effect adoption failed: {run_id} {effect_key}")


__all__ = ["ReconcileError", "reconcile"]
