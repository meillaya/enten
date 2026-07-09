"""Smoke test for the scaffold.

Real tests land in T06. This single sanity test exists so that
``uv run pytest`` exits 0 from the very first commit and CI has a
deterministic green starting point.
"""

from __future__ import annotations


def test_version_is_scaffold() -> None:
    """Version string should be the 0.1.0 scaffold release."""
    from enten import __version__

    assert __version__ == "0.1.0"
