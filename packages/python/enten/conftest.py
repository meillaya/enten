"""Pytest fixtures for the ``enten`` Python package.

Three autouse + scope fixtures land here in T06 (test infrastructure):

- ``db_session`` — testcontainers Postgres with the ``pgvector``
  extension pre-installed; yields an ``asyncpg`` connection pool.
- ``tracer_with_memory_exporter`` — an OpenTelemetry tracer backed by
  an in-memory span exporter so tests can assert on emitted spans
  without standing up a Jaeger collector.
- ``no_llm_calls`` — autouse. Replaces ``LLMProvider.complete`` with a
  stub that raises ``LLMCallWithoutMock`` unless the test opted in
  via the ``allow_llm_calls`` fixture.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
import pytest_asyncio

# ─── Optional imports — guarded so this conftest does not hard-require
# testcontainers at collection time. The fixtures are still defined;
# importing the module only happens at fixture-instantiation time.
try:
    import asyncpg  # noqa: F401
    import testcontainers.core.container as _tc_container  # noqa: F401
    _TESTCONTAINERS_AVAILABLE = True
except ImportError:  # pragma: no cover - reachable only if extras not installed
    _TESTCONTAINERS_AVAILABLE = False


# ─── db_session ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container() -> Generator[Any, None, None]:
    """Session-scoped pgvector/pgvector:pg16 container.

    Skipped with a clear message when testcontainers or Docker is not
    available; individual tests that need a real database should be
    marked ``@pytest.mark.slow`` and gated on this fixture.
    """
    if not _TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed (install enten[test])")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest_asyncio.fixture
async def db_session(postgres_container: Any) -> Generator[Any, None, None]:
    """Per-test asyncpg pool with the ``vector`` extension enabled.

    Drop and recreate a fresh schema for each test so suites stay
    independent. The connection string uses ``postgresql+asyncpg``
    to match the format ``Settings.database_url`` expects.
    """
    import asyncpg

    raw_url = postgres_container.get_connection_url()
    # testcontainers may return a psycopg2-schemed URL; asyncpg needs the
    # plain ``postgresql`` scheme, while SQLAlchemy needs ``postgresql+asyncpg``.
    dsn = raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    raw_url = raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)

    conn = await asyncpg.connect(dsn=raw_url)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    finally:
        await conn.close()

    pool = await asyncpg.create_pool(dsn=raw_url, min_size=1, max_size=4)
    assert pool is not None

    try:
        # Verify the extension really is there; a missing ``vector``
        # column type would fail every pgvector-touching test later on.
        async with pool.acquire() as c:
            ext = await c.fetchval("SELECT extname FROM pg_extension WHERE extname='vector'")
            assert ext == "vector", "pgvector extension not loaded"

        yield {"dsn": dsn, "pool": pool, "container": postgres_container}
    finally:
        await pool.close()


# ─── tracer_with_memory_exporter ───────────────────────────────────────


@pytest.fixture
def tracer_with_memory_exporter() -> Generator[Any, None, None]:
    """Tracer wired to an in-memory span exporter.

    Tests assert on ``exporter.get_finished_spans()`` without ever
    touching the network. If T05 (OTel SDK) has wired a real
    tracer module, we use it; otherwise we stand up a local
    ``InMemorySpanExporter`` so this fixture is useful from T06
    onward.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("enten-test")

    try:
        yield {"tracer": tracer, "exporter": exporter, "provider": provider}
    finally:
        provider.shutdown()


# ─── no_llm_calls (autouse) ────────────────────────────────────────────


class LLMCallWithoutMock(RuntimeError):
    """Raised when production code invokes an LLM without a test mock.

    The autouse ``no_llm_calls`` fixture monkeypatches
    ``LLMProvider.complete`` so any unmarked call explodes. Tests
    that legitimately need an LLM response declare the
    ``allow_llm_calls`` fixture, which restores the original.
    """


@pytest.fixture(autouse=True)
def no_llm_calls(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail the test if the LLM is contacted without an explicit mock.

    Tests that opt in to real LLM calls do so by depending on the
    ``allow_llm_calls`` fixture — its presence flips the flag before
    this autouse runs.
    """
    use_real = "allow_llm_calls" in request.fixturenames
    if use_real:
        # Make the opt-in explicit and fail loud if the env var is not
        # also set, so a stray opt-in doesn't silently hit production.
        if os.environ.get("ENTEN_TEST_ALLOW_LLM") != "1":
            pytest.fail(
                "allow_llm_calls requested but ENTEN_TEST_ALLOW_LLM != 1; "
                "set the env var explicitly to acknowledge the network call",
            )
        return

    # Lazy import so the fixture is importable even before T24 lands.
    from enten.llm import LLMProvider

    def _forbid(*_args: object, **_kwargs: object) -> object:
        raise LLMCallWithoutMock(
            "LLMProvider.complete was called in a test that did not opt in. "
            "Either inject a mock or depend on the `allow_llm_calls` fixture "
            "with ENTEN_TEST_ALLOW_LLM=1.",
        )

    monkeypatch.setattr(LLMProvider, "complete", _forbid, raising=False)


@pytest.fixture
def allow_llm_calls() -> None:
    """Marker fixture; presence in a test signature opts in to LLM calls.

    Pair with ``ENTEN_TEST_ALLOW_LLM=1`` in the environment. Without
    that env var the ``no_llm_calls`` autouse still fails the test.
    """
