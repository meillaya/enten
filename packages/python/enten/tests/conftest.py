"""Shared pytest fixtures for the ``enten`` Python package.

Scaffold fixture set only — T06 (test infrastructure) will populate this
with real fixtures (tmp dirs, async runners, golden diffs, etc.).
"""

from __future__ import annotations

import pytest

from enten import __version__


@pytest.fixture(scope="session")
def enten_version() -> str:
    """Expose the package version to tests for sanity checks."""
    return __version__
