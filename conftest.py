"""Root pytest configuration for the enten monorepo.

Loaded for every pytest invocation, regardless of the package being
tested. Three responsibilities:

1. Stub environment variables so settings (T03) can be imported without
   a real ``.env`` file. The stubs are deterministic, non-secret, and
   safe to commit.
2. Define session-scoped fixtures for sharing env access and a
   monorepo root path.
3. Filter known harmless warnings from third-party libraries that fire
   during test collection.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

ENV_STUBS: dict[str, str] = {
    "GITHUB_APP_ID": "12345",
    "GITHUB_APP_PRIVATE_KEY_PATH": "/tmp/enten-test-fake.pem",
    "GITHUB_WEBHOOK_SECRET": "test-webhook-secret",
    "LLM_PROVIDER": "openai-compat",
    "LLM_OPENAI_BASE_URL": "https://api.openai.com/v1",
    "LLM_OPENAI_API_KEY": "sk-test-fake-key",
    "LLM_OLLAMA_BASE_URL": "http://localhost:11434",
    "SANDBOX_PROVIDER": "docker",
    "DATABASE_URL": "postgresql+asyncpg://enten@localhost:5432/enten",
    "OTEL_ENDPOINT": "http://localhost:4317",
    "PER_PR_COST_CAP_USD": "0.50",
}


def pytest_configure(config: pytest.Config) -> None:
    """Apply env stubs exactly once per pytest session.

    Honors any pre-existing real values so a developer with a ``.env``
    file can still run tests against their own config.
    """
    for key, value in ENV_STUBS.items():
        os.environ.setdefault(key, value)

    config.addinivalue_line(
        "markers",
        "network: tests that intentionally hit external network (off by default).",
    )
    config.addinivalue_line(
        "markers",
        "slow: tests that take > 1s (e.g. testcontainers spin-up).",
    )


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the monorepo root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def test_env() -> dict[str, str]:
    """Read-only view of the env-stub values applied by this conftest."""
    return dict(ENV_STUBS)


@pytest.fixture(autouse=True)
def _suppress_noisy_warnings(recwarn: pytest.WarningsRecorder) -> None:
    """Filter known harmless warnings emitted during test collection.

    Subpackages can extend this list by adding their own filters in
    their own conftest.py — pytest composes them.
    """
    warnings.filterwarnings(
        "ignore",
        message=".*Deprecated.*",
        category=DeprecationWarning,
        module=r"(opentelemetry|testcontainers|docker).*",
    )
