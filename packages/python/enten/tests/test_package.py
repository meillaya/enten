from __future__ import annotations


def test_package_version() -> None:
    from enten import __version__

    assert __version__ == "0.1.0"
