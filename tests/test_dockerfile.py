"""Smoke tests for the production Dockerfile.

These tests are skipped when Docker is not available so they do not fail
in environments without a daemon (e.g. lightweight lint runs).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _docker_available() -> bool:
    return shutil.which("docker") is not None and _docker_daemon_responds()


def _docker_daemon_responds() -> bool:
    try:
        _ = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


@pytest.fixture(scope="module")
def image_tag() -> str:
    return f"enten-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def built_image(image_tag: str) -> Iterator[dict[str, Any]]:
    if not _docker_available():
        pytest.skip("Docker daemon not available")

    _ = subprocess.run(
        ["docker", "build", "-t", image_tag, "-f", "Dockerfile", "."],
        cwd=REPO_ROOT,
        check=True,
    )

    inspect = subprocess.run(
        ["docker", "inspect", image_tag],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    metadata: dict[str, Any] = json.loads(inspect.stdout)[0]

    yield metadata

    _ = subprocess.run(
        ["docker", "rmi", "-f", image_tag],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_dockerfile_runs_as_non_root(built_image: dict[str, Any]) -> None:
    config = built_image["Config"]
    user = config.get("User", "")
    assert user == "1000:1000", f"expected USER 1000:1000, got {user!r}"


def test_runtime_resolves_production_dependencies(
    built_image: dict[str, Any],
    image_tag: str,
) -> None:
    """The runtime image can import a workspace production dependency."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            image_tag,
            "--input-type=module",
            "-e",
            "await import('@hono/node-server')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"runtime dependency resolution failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_runtime_has_checkout_tools(
    built_image: dict[str, Any],
    image_tag: str,
) -> None:
    """The worker image can run git and the bounded fetch helper."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            image_tag,
            "-c",
            "git --version >/dev/null && prlimit --version >/dev/null 2>&1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"checkout tools are missing:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_dockerfile_has_healthcheck(built_image: dict[str, Any]) -> None:
    healthcheck = built_image["Config"].get("Healthcheck")
    assert healthcheck is not None, "HEALTHCHECK is not defined"
    test_cmd = healthcheck.get("Test", [])
    assert "python" in " ".join(test_cmd) and "/app/health.py" in " ".join(test_cmd), (
        f"expected health check to use python /app/health.py, got {test_cmd!r}"
    )


def test_dockerfile_has_image_title_label(built_image: dict[str, Any]) -> None:
    labels = built_image["Config"].get("Labels") or {}
    assert labels.get("org.opencontainers.image.title") == "enten", (
        f"missing org.opencontainers.image.title label, got {labels!r}"
    )
