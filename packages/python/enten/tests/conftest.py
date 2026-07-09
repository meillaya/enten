"""Shared pytest fixtures for the ``enten`` Python package."""

from __future__ import annotations

import pytest

from enten import __version__


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network: tests that intentionally hit external network (off by default).",
    )
    config.addinivalue_line(
        "markers",
        "slow: tests that take > 1s (e.g. testcontainers spin-up).",
    )


@pytest.fixture(scope="session")
def enten_version() -> str:
    """Expose the package version to tests for sanity checks."""
    return __version__
