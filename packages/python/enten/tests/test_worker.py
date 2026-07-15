"""End-to-end tests for the enten worker process.

These tests spin up a real Postgres container via testcontainers, apply the
T08 migration, and exercise the worker's claim/run/retry/recovery paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from enten import worker as worker_mod
from enten.github.models import CheckRun, CheckRunOutput, Comment
from enten.migrations import apply_migrations
from enten.queue import claim_next, enqueue, init_queue, mark_state
from enten.reporting.effects import FencedGitHubEffects, NoopEffects
from enten.review.postgres_store import PostgresReviewStore
from enten.review.store import JsonReviewStore
from enten.worker import build_pipeline_dependencies, clean_stale_jobs, run_pipeline

MIGRATIONS = Path(__file__).parents[4] / "migrations"


async def _reset_schema(pool: Any) -> None:
    """Drop all application tables so each test starts from a clean slate."""
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await conn.execute("DROP SCHEMA IF EXISTS vec CASCADE")


async def _apply_migration(pool: Any) -> None:
    """Run the T08 migration SQL against a fresh testcontainers pool."""
    async with pool.acquire() as conn:
        await apply_migrations(conn, MIGRATIONS)


@pytest_asyncio.fixture
async def migrated_db(db_session: Any) -> Any:
    """Provide a test DB pool with the full schema and queue initialized."""
    await _reset_schema(db_session["pool"])
    await _apply_migration(db_session["pool"])
    init_queue(db_session["pool"])
    yield db_session


async def test_worker_picks_up_job_and_transitions_to_done(
    migrated_db: Any, tmp_path: Path
) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {"pr_number": 1}})

    job = await claim_next("worker-test")
    assert job is not None
    assert job["run_id"] == run_id
    assert job["state"] == "indexing"

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n", encoding="utf-8")
    await run_pipeline(
        run_id,
        dependencies=build_pipeline_dependencies(
            run_id,
            generation=job["generation"],
            store_path=tmp_path / "reviews.json",
        ),
        repository_path=repository,
    )

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "done"
    assert row["attempts"] == 0


def test_production_dependencies_use_fenced_postgres_state() -> None:
    class Client:
        async def create_check_run(
            self,
            owner: str,
            repo: str,
            sha: str,
            conclusion: str,
            output: CheckRunOutput,
        ) -> CheckRun:
            raise AssertionError((owner, repo, sha, conclusion, output))

        async def post_pr_comment(
            self,
            owner: str,
            repo: str,
            number: int,
            body: str,
            marker: str = "<!-- enten-bot -->",
        ) -> Comment:
            raise AssertionError((owner, repo, number, body, marker))

    dependencies = build_pipeline_dependencies(
        "queue-run-1",
        generation=7,
        github_client=Client(),
    )

    assert isinstance(dependencies.store, PostgresReviewStore)
    assert isinstance(dependencies.effects, FencedGitHubEffects)


def test_explicit_local_store_remains_file_backed(tmp_path: Path) -> None:
    dependencies = build_pipeline_dependencies(
        "local-run-1",
        effects=NoopEffects(),
        store_path=tmp_path / "reviews.json",
    )

    assert isinstance(dependencies.store, JsonReviewStore)


async def test_worker_retries_on_exception(
    migrated_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing pipeline is requeued with an incremented attempt count."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})

    async def _boom(_rid: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "run_pipeline", _boom)

    job = await claim_next("worker-test")
    assert job is not None

    try:
        await worker_mod.run_pipeline(run_id)
    except Exception as exc:
        await worker_mod.mark_failed(
            run_id,
            exc,
            retry=True,
            generation=job["generation"],
        )

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "queued"
    assert row["attempts"] == 1


async def test_worker_fails_permanently_after_three_attempts(
    migrated_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After three failed attempts the job stays failed permanently."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})

    async def _boom(_rid: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "run_pipeline", _boom)

    for _ in range(3):
        job = await claim_next("worker-test")
        assert job is not None
        assert job["run_id"] == run_id

        try:
            await worker_mod.run_pipeline(run_id)
        except Exception as exc:
            await worker_mod.mark_failed(
                run_id,
                exc,
                retry=True,
                generation=job["generation"],
            )

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT state, attempts FROM jobs WHERE run_id = $1", run_id)

    assert row is not None
    assert row["state"] == "failed"
    assert row["attempts"] == 3


async def test_clean_stale_jobs_resets_stale_indexing_job(migrated_db: Any) -> None:
    """A job stuck in ``indexing`` longer than the threshold is requeued."""
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    await mark_state(run_id, "indexing")

    async with migrated_db["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET updated_at = now() - interval '30 minutes' WHERE run_id = $1",
            run_id,
        )

    count = await clean_stale_jobs(threshold_minutes=15)
    assert count == 1

    async with migrated_db["pool"].acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state, attempts, claimed_by, lease_expires_at, last_error
            FROM jobs
            WHERE run_id = $1
            """,
            run_id,
        )

    assert row is not None
    assert row["state"] == "queued"
    assert row["attempts"] == 1
    assert row["claimed_by"] is None
    assert row["lease_expires_at"] is None
    assert "stale" in row["last_error"]


async def test_repeated_stale_expiries_exhaust_retry_budget_without_starvation(
    migrated_db: Any,
) -> None:
    expired_run_id = await enqueue({"kind": "pr_review", "payload": {}})

    for expected_attempts in range(1, 4):
        claim = await claim_next(f"crashing-worker-{expected_attempts}")
        assert claim is not None
        assert claim["run_id"] == expired_run_id
        async with migrated_db["pool"].acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET lease_expires_at = now() - interval '1 second'
                WHERE run_id = $1
                """,
                expired_run_id,
            )

        assert await claim_next("bypass-worker") is None
        assert await clean_stale_jobs() == 1

        async with migrated_db["pool"].acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state, attempts FROM jobs WHERE run_id = $1",
                expired_run_id,
            )
        assert row is not None
        assert row["attempts"] == expected_attempts
        expected_state = "queued" if expected_attempts < 3 else "failed"
        assert row["state"] == expected_state

    fresh_run_id = await enqueue({"kind": "pr_review", "payload": {}})
    next_claim = await claim_next("healthy-worker")

    assert next_claim is not None
    assert next_claim["run_id"] == fresh_run_id


async def test_expired_claim_does_not_starve_queued_job(migrated_db: Any) -> None:
    expired_run_id = await enqueue({"kind": "pr_review", "payload": {}})
    expired_claim = await claim_next("crashing-worker")
    assert expired_claim is not None
    async with migrated_db["pool"].acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET lease_expires_at = now() - interval '1 second'
            WHERE run_id = $1
            """,
            expired_run_id,
        )
    fresh_run_id = await enqueue({"kind": "pr_review", "payload": {}})

    fresh_claim = await claim_next("healthy-worker")

    assert fresh_claim is not None
    assert fresh_claim["run_id"] == fresh_run_id
    assert await mark_state(
        fresh_run_id,
        "done",
        generation=fresh_claim["generation"],
    )
    assert await clean_stale_jobs() == 1


async def test_clean_stale_jobs_does_not_reclaim_unexpired_lease(migrated_db: Any) -> None:
    run_id = await enqueue({"kind": "pr_review", "payload": {}})
    job = await claim_next("slow-worker")
    assert job is not None
    async with migrated_db["pool"].acquire() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET updated_at = now() - interval '1 hour',
                lease_expires_at = now() + interval '5 minutes'
            WHERE run_id = $1
            """,
            run_id,
        )

    count = await clean_stale_jobs(threshold_minutes=15)
    competing_claim = await claim_next("competing-worker")

    assert count == 0
    assert competing_claim is None


@pytest.mark.parametrize(
    ("app_id", "private_key", "missing_name"),
    [
        (None, Path("/tmp/app.pem"), "GITHUB_APP_ID"),
        (123, None, "GITHUB_APP_PRIVATE_KEY_PATH"),
    ],
)
def test_production_worker_fails_closed_without_github_app_credentials(
    app_id: int | None,
    private_key: Path | None,
    missing_name: str,
) -> None:
    from enten.config import Settings

    settings = Settings.model_validate(
        {
            "github_app_id": app_id,
            "github_app_private_key_path": private_key,
        }
    )

    with pytest.raises(worker_mod.WorkerConfigurationError, match=missing_name):
        worker_mod.build_production_github_client(settings)


def test_worker_parses_trusted_verification_command() -> None:
    from enten.config import Settings

    settings = Settings(verification_command="pytest -q 'tests/unit tests'")

    assert worker_mod.parse_verification_command(settings) == (
        "pytest",
        "-q",
        "tests/unit tests",
    )


def test_worker_rejects_empty_verification_command() -> None:
    from enten.config import Settings

    settings = Settings(verification_command="   ")

    with pytest.raises(worker_mod.WorkerConfigurationError, match="VERIFICATION_COMMAND"):
        worker_mod.parse_verification_command(settings)


async def test_worker_bootstrap_normalizes_the_shipped_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from enten import _worker_runtime as runtime_mod
    from enten.config import Settings

    captured: dict[str, object] = {}

    class FakeGitHubClient:
        async def close(self) -> None:
            captured["github_closed"] = True

    async def create_pool(dsn: str, *, min_size: int, max_size: int) -> object:
        captured.update(dsn=dsn, min_size=min_size, max_size=max_size)
        return object()

    monkeypatch.setattr(
        runtime_mod,
        "build_production_github_client",
        lambda _settings: FakeGitHubClient(),
    )
    monkeypatch.setattr("enten.sandbox.factory.get_provider", lambda _settings: object())
    monkeypatch.setattr(runtime_mod.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(runtime_mod, "init_queue", lambda pool: captured.update(pool=pool))
    settings = Settings(
        sandbox_docker_image="example.test/verifier@sha256:" + "a" * 64,
        database_url="postgresql+asyncpg://enten:enten@postgres:5432/enten",
    )

    runtime = await runtime_mod.bootstrap_worker(settings)

    assert captured["dsn"] == "postgresql://enten:enten@postgres:5432/enten"
    assert captured["min_size"] == 1
    assert captured["max_size"] == 4
    assert captured["pool"] is runtime.pool
